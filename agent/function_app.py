"""
agent/function_app.py

Unified Azure Functions entry point for the Foundry backend.

Routes:
    POST /api/ingest-mppr   — Upload an MPPR Excel file → index in Azure AI Search
    POST /api/validate      — Validate a narrative using the AI Foundry Agent

Authentication (both locally and in Azure):
    Uses DefaultAzureCredential — no secrets needed in settings.
    - Locally: picks up your `az login` session automatically.
    - In Azure: uses the Function App's System-Assigned Managed Identity.
"""

import json
import logging

import azure.functions as func

from ingest_helper import ensure_index_exists, run_ingest, upload_eac_file
from agent_runner import validate_narrative

logger = logging.getLogger(__name__)

# Ensure the AI Search index exists on cold start (no-op if it already exists)
try:
    ensure_index_exists()
except Exception as _exc:
    logger.warning("Could not auto-create AI Search index on startup: %s", _exc)


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/ingest-mppr
# Body: multipart/form-data
#   - file            : the .xlsx MPPR file
#   - reporting_period: e.g. "P07 2025-26"
# ─────────────────────────────────────────────────────────────────────────────
@app.route(route="ingest-mppr", methods=["POST"])
def ingest_mppr(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("ingest-mppr triggered.")

    file_data = req.files.get("file")
    if not file_data:
        return func.HttpResponse(
            json.dumps({"error": "No file provided. Send an .xlsx file in the 'file' field."}),
            status_code=400,
            mimetype="application/json",
        )

    reporting_period = req.form.get("reporting_period", "Unknown")

    try:
        file_bytes = file_data.read()
    except Exception as exc:
        logger.exception("Failed to read uploaded file.")
        return func.HttpResponse(
            json.dumps({"error": f"Failed to read file: {exc}"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        result = run_ingest(file_bytes, reporting_period)
    except Exception as exc:
        logger.exception("Ingestion pipeline failed.")
        return func.HttpResponse(
            json.dumps({"error": f"Ingestion failed: {exc}"}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps(result),
        status_code=200,
        mimetype="application/json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/ingest-eac
# Body: multipart/form-data
#   - file: the lifecycle_eac_variance.xlsx file
# Uploads the file to Azure Blob Storage. tools.py detects the new ETag on the
# next call and automatically refreshes its in-memory cache.
# ─────────────────────────────────────────────────────────────────────────────
@app.route(route="ingest-eac", methods=["POST"])
def ingest_eac(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("ingest-eac triggered.")

    file_data = req.files.get("file")
    if not file_data:
        return func.HttpResponse(
            json.dumps({"error": "No file provided. Send the .xlsx file in the 'file' field."}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        file_bytes = file_data.read()
    except Exception as exc:
        logger.exception("Failed to read uploaded EAC file.")
        return func.HttpResponse(
            json.dumps({"error": f"Failed to read file: {exc}"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        result = upload_eac_file(file_bytes)
    except Exception as exc:
        logger.exception("EAC file upload to blob storage failed.")
        return func.HttpResponse(
            json.dumps({"error": f"Upload failed: {exc}"}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps(result),
        status_code=200,
        mimetype="application/json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/validate
# Body: application/json
#   {
#     "project_name"    : "Dounreay Shaft and Silo",       -- required
#     "narrative"       : "The SRO DCA remains Amber ...", -- required
#     "period"          : "P07 2025-26",                   -- optional
#
#     -- Conversation memory fields (both optional):
#     "conversation_id" : "azure-conv-uuid",  -- null/absent = start new session
#     "user_scope"      : "user-abc-123"      -- scopes Memory Store per user
#   }
#
# Response:
#   {
#     "conversation_id"     : "azure-conv-uuid",  -- store client-side for follow-ups
#     "is_new_conversation" : true,
#     "validation_result"   : "..."
#   }
# ─────────────────────────────────────────────────────────────────────────────
@app.route(route="validate", methods=["POST"])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    """
    Validate a narrative using the AI Foundry agent with conversation memory.

    On the first call omit conversation_id (or pass null) — the server creates
    a new conversation session and returns its ID.  On follow-up calls pass the
    returned conversation_id to let the agent remember the original narrative
    and any previous exchanges (e.g. "now fix just the EAC sentence").
    """
    logger.info("validate triggered.")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Request body must be valid JSON."}),
            status_code=400,
            mimetype="application/json",
        )

    project_name    = body.get("project_name", "")
    narrative_text  = body.get("narrative", "")
    period          = body.get("period", "")
    # Short-term session memory — pass back the ID from a previous response
    conversation_id = body.get("conversation_id") or None
    # Long-term Memory Store scope — use a stable user/tenant identifier
    user_scope      = body.get("user_scope") or None

    if not narrative_text:
        return func.HttpResponse(
            json.dumps({"error": "'narrative' field is required."}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        result = validate_narrative(
            project_name=project_name,
            narrative_text=narrative_text,
            period=period,
            conversation_id=conversation_id,
            user_scope=user_scope,
        )
    except Exception as exc:
        logger.exception("Agent validation failed.")
        return func.HttpResponse(
            json.dumps({"error": f"Validation failed: {exc}"}),
            status_code=500,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps(result),
        status_code=200,
        mimetype="application/json",
    )
