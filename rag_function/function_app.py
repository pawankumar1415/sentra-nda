"""
rag_function/function_app.py — Azure Functions v2 entry point.

Two HTTP routes:
    POST /api/ingest    — Upload Excel → embed → upsert to PGVector
    POST /api/validate  — Validate narrative → RAG + EAC → GPT response

Cold-start initialisation:
    - Ensures PostgreSQL schema exists (table + pgvector index)
    - All heavy imports (psycopg2, openai, pandas) happen at module load
"""

import json
import logging

import azure.functions as func

from db import ensure_schema
from ingest import run_ingest, list_projects_from_bytes
from ingest_eac import run_ingest_eac
from validate import run_validate
from batch_validate import run_batch_validate
from chat import run_chat

logger = logging.getLogger(__name__)

# ── App registration ───────────────────────────────────────────────────────────
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# ── Cold-start: ensure DB schema exists ───────────────────────────────────────
try:
    ensure_schema()
    logger.info("Cold-start DB schema check: OK")
except Exception as _e:
    logger.error("Cold-start DB schema check FAILED: %s", _e)
    # Don't raise — let the function start and surface errors per-request


# ── Route 1: Ingest ───────────────────────────────────────────────────────────
@app.route(route="ingest", methods=["POST"])
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/ingest

    Accepts an Excel file (5a)NDA MPPR sheet) either as:
      - multipart/form-data with field name 'file'
      - raw binary body (Content-Type: application/octet-stream)

    Returns JSON: { "status": "ok", "period": "P07", "indexed": 42 }
    """
    logger.info("POST /api/ingest — request received")

    try:
        # Accept multipart upload OR raw binary body
        file_bytes: bytes = b""
        filename: str = ""

        files = req.files
        if files and "file" in files:
            uploaded = files["file"]
            file_bytes = uploaded.read()
            filename = getattr(uploaded, "filename", "") or ""
        else:
            file_bytes = req.get_body()
            # Allow filename via query param for raw body uploads
            filename = req.params.get("filename", "")

        if not file_bytes:
            return func.HttpResponse(
                json.dumps({"error": "No file provided. "
                            "Send Excel as multipart field 'file' or raw body."}),
                status_code=400,
                mimetype="application/json",
            )

        result = run_ingest(file_bytes, filename=filename)

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )

    except ValueError as exc:
        # Bad input (e.g. wrong sheet name)
        logger.warning("Ingest validation error: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": str(exc)}),
            status_code=400,
            mimetype="application/json",
        )
    except Exception as exc:
        logger.exception("Ingest pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


# ── Route 2: Ingest EAC ───────────────────────────────────────────────────────
@app.route(route="ingest-eac", methods=["POST"])
def ingest_eac(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/ingest-eac

    Accepts an Excel file (lifecycle_eac_variance.xlsx) either as:
      - multipart/form-data with field name 'file'
      - raw binary body
    """
    logger.info("POST /api/ingest-eac — request received")
    
    try:
        file_bytes: bytes = b""
        files = req.files
        if files and "file" in files:
            file_bytes = files["file"].read()
        else:
            file_bytes = req.get_body()

        if not file_bytes:
            return func.HttpResponse(
                json.dumps({"error": "No file provided"}),
                status_code=400,
                mimetype="application/json",
            )

        result = run_ingest_eac(file_bytes)

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as exc:
        logger.exception("EAC Ingest pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


# ── Route 3: Validate ─────────────────────────────────────────────────────────
@app.route(route="validate", methods=["POST"])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/validate

    Request body (JSON):
    {
        "narrative":     "<the narrative text to validate>",
        "project_name":  "<project name for data lookup>",
        "period":        "P07"   (optional — used for EAC lookup)
        "top_k":         5       (optional — number of similar chunks to retrieve)
    }

    Returns JSON with Layer 1 + Layer 2 validation results.
    """
    logger.info("POST /api/validate — request received")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Request body must be valid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    narrative    = body.get("narrative", "").strip()
    project_name = body.get("project_name", "").strip()
    period       = body.get("period", None)
    top_k        = int(body.get("top_k", 5))

    if not narrative:
        return func.HttpResponse(
            json.dumps({"error": "'narrative' field is required"}),
            status_code=400,
            mimetype="application/json",
        )
    if not project_name:
        return func.HttpResponse(
            json.dumps({"error": "'project_name' field is required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        result = run_validate(
            narrative=narrative,
            project_name=project_name,
            period=period,
            top_k=top_k,
        )
        return func.HttpResponse(
            json.dumps(result, indent=2),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as exc:
        logger.exception("Validation pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )

# ── Route 4: Chat ─────────────────────────────────────────────────────────────
@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/chat

    Conversational RAG endpoint with server-side session memory.

    Request body (JSON):
    {
        "question":   "What is the portfolio health?",     -- required
        "session_id": "550e8400-e29b-41d4-a716-446655440000"  -- optional UUID

        -- Legacy fallback (ignored when session_id is valid):
        "history": [
            {"role": "user",      "content": "hello"},
            {"role": "assistant", "content": "hi"}
        ]
    }

    Response body (JSON):
    {
        "answer":     "In P07, the portfolio shows ...",   -- markdown string
        "session_id": "550e8400-e29b-41d4-a716-446655440000",  -- store in localStorage
        "meta": {
            "intent":            "portfolio_summary",
            "projects_detected": [],
            "context_length":    1243,
            "is_new_session":    true
        }
    }

    Session lifecycle:
      - First call: omit session_id (or send null) → server creates a new session
        and returns session_id in the response.
      - Subsequent calls: send the returned session_id → server loads history from
        PostgreSQL and continues the conversation.
      - New conversation: send session_id=null again (or a fresh UUID the server
        won't recognise) → server creates a new session.
    """
    logger.info("POST /api/chat — request received")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Request body must be valid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    question   = body.get("question", "").strip()
    session_id = body.get("session_id") or None   # treat empty string as None
    # Legacy history array — used only when session_id is absent/invalid
    history    = body.get("history", [])

    if not question:
        return func.HttpResponse(
            json.dumps({"error": "'question' field is required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        result = run_chat(
            question=question,
            session_id=session_id,
            history=history,
        )
        return func.HttpResponse(
            json.dumps(result, indent=2),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as exc:
        logger.exception("Chat pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )

# ── Route 5: List Projects ────────────────────────────────────────────────────
@app.route(route="list-projects", methods=["POST"])
def list_projects(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/list-projects

    Accepts an Excel file. Returns lightweight period and project metadata.
    """
    logger.info("POST /api/list-projects — request received")

    try:
        file_bytes: bytes = b""
        filename: str = ""

        files = req.files
        if files and "file" in files:
            uploaded = files["file"]
            file_bytes = uploaded.read()
            filename = getattr(uploaded, "filename", "") or ""
        else:
            file_bytes = req.get_body()
            filename = req.params.get("filename", "")

        if not file_bytes:
            return func.HttpResponse(
                json.dumps({"error": "No file provided. Send Excel as multipart field 'file' or raw body."}),
                status_code=400,
                mimetype="application/json",
            )

        result = list_projects_from_bytes(file_bytes, filename=filename)

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as exc:
        logger.exception("List projects pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


# ── Route 6: Batch Validate ───────────────────────────────────────────────────
@app.route(route="batch-validate", methods=["POST"])
def batch_validate(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/batch-validate

    Accepts an Excel file. Runs validation on every narrative found.
    """
    logger.info("POST /api/batch-validate — request received")

    try:
        file_bytes: bytes = b""
        filename: str = ""

        files = req.files
        if files and "file" in files:
            uploaded = files["file"]
            file_bytes = uploaded.read()
            filename = getattr(uploaded, "filename", "") or ""
        else:
            file_bytes = req.get_body()
            filename = req.params.get("filename", "")

        if not file_bytes:
            return func.HttpResponse(
                json.dumps({"error": "No file provided. Send Excel as multipart field 'file' or raw body."}),
                status_code=400,
                mimetype="application/json",
            )

        # Allow passing top_k via query param, defaulting to 5
        top_k = int(req.params.get("top_k", 5))

        result = run_batch_validate(file_bytes, filename=filename, top_k=top_k)

        return func.HttpResponse(
            json.dumps(result),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as exc:
        logger.exception("Batch validate pipeline failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Internal error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )
