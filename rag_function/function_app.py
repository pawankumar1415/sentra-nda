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

from .db import ensure_schema
from .ingest import run_ingest
from .validate import run_validate

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

        files = req.files
        if files and "file" in files:
            file_bytes = files["file"].read()
        else:
            file_bytes = req.get_body()

        if not file_bytes:
            return func.HttpResponse(
                json.dumps({"error": "No file provided. "
                            "Send Excel as multipart field 'file' or raw body."}),
                status_code=400,
                mimetype="application/json",
            )

        result = run_ingest(file_bytes)

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


# ── Route 2: Validate ─────────────────────────────────────────────────────────
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
