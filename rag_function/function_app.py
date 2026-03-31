"""
rag_function/function_app.py — Azure Functions v2 entry point.

Routes:
    POST /api/auth/register       — Create account (open)
    POST /api/auth/login          — Obtain JWT token

    GET  /api/mgmt/users         — List all users with stats       [admin]
    POST /api/mgmt/users/update  — Toggle active / admin flag      [admin]
    POST /api/mgmt/users/delete  — Delete user and all their data  [admin]

    POST /api/ingest              — Upload MPPR Excel → embed → PGVector  [auth]
    POST /api/ingest-eac          — Upload EAC variance Excel              [auth]
    POST /api/validate            — Validate single narrative              [auth]
    POST /api/chat                — Conversational RAG with session memory [auth]
    POST /api/list-projects              — Extract project list from Excel        [auth]
    GET  /api/search-projects            — Fuzzy search indexed project names     [auth]
    POST /api/batch-validate             — Validate every narrative in an Excel   [auth]

    GET  /api/sharepoint/files           — List Excel files from SharePoint list  [auth]
    POST /api/sharepoint/list-projects   — Download SP file, extract project list [auth]
"""

import json
import logging

import azure.functions as func

from auth import (
    register_user, login_user,
    require_auth, require_admin,
    list_users, update_user, delete_user,
)
from db import ensure_schema, search_projects
from ingest import run_ingest, list_projects_from_bytes
from ingest_eac import run_ingest_eac
from validate import run_validate
from batch_validate import run_batch_validate
from chat import run_chat
from sharepoint_client import list_files as sp_list_files, download_file as sp_download_file

logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# ── Cold-start: ensure DB schema exists ───────────────────────────────────────
try:
    ensure_schema()
    logger.info("Cold-start DB schema check: OK")
except Exception as _e:
    logger.error("Cold-start DB schema check FAILED: %s", _e)


# ── Helper: standard error responses ──────────────────────────────────────────

def _err(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )


def _ok(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data),
        status_code=status,
        mimetype="application/json",
    )


# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="auth/register", methods=["POST"])
def auth_register(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/auth/register — { username, password } → { token, username, is_admin }"""
    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    username = body.get("username", "").strip()
    password = body.get("password", "")

    try:
        result = register_user(username, password)
        return _ok(result, 201)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("Register failed: %s", exc)
        return _err("Registration failed. Please try again.", 500)


@app.route(route="auth/login", methods=["POST"])
def auth_login(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/auth/login — { username, password } → { token, username, is_admin }"""
    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    username = body.get("username", "").strip()
    password = body.get("password", "")

    try:
        result = login_user(username, password)
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 401)
    except Exception as exc:
        logger.exception("Login failed: %s", exc)
        return _err("Login failed. Please try again.", 500)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="mgmt/users", methods=["GET"])
def admin_list_users(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/mgmt/users — Returns all users with stats. Admin only."""
    try:
        require_admin(req)
    except PermissionError as exc:
        return _err(str(exc), 403)

    try:
        users = list_users()
        return _ok({"users": users})
    except Exception as exc:
        logger.exception("admin_list_users failed: %s", exc)
        return _err("Failed to retrieve users.", 500)


@app.route(route="mgmt/users/update", methods=["POST"])
def admin_update_user(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/mgmt/users/update — { user_id, is_active?, is_admin? }. Admin only."""
    try:
        admin = require_admin(req)
    except PermissionError as exc:
        return _err(str(exc), 403)

    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    target_user_id = body.get("user_id", "").strip()
    if not target_user_id:
        return _err("'user_id' is required", 400)

    kwargs = {}
    if "is_active" in body:
        kwargs["is_active"] = bool(body["is_active"])
    if "is_admin" in body:
        kwargs["is_admin"] = bool(body["is_admin"])

    try:
        result = update_user(target_user_id, admin["user_id"], **kwargs)
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("admin_update_user failed: %s", exc)
        return _err("Failed to update user.", 500)


@app.route(route="mgmt/users/delete", methods=["POST"])
def admin_delete_user(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/mgmt/users/delete — { user_id }. Admin only."""
    try:
        admin = require_admin(req)
    except PermissionError as exc:
        return _err(str(exc), 403)

    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    target_user_id = body.get("user_id", "").strip()
    if not target_user_id:
        return _err("'user_id' is required", 400)

    try:
        result = delete_user(target_user_id, admin["user_id"])
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("admin_delete_user failed: %s", exc)
        return _err("Failed to delete user.", 500)


# ══════════════════════════════════════════════════════════════════════════════
# DATA ROUTES  (all require a valid JWT)
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="ingest", methods=["POST"])
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/ingest — Upload MPPR Excel → embed → upsert to PGVector."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

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
            return _err("No file provided. Send Excel as multipart field 'file' or raw body.", 400)

        result = run_ingest(file_bytes, filename=filename, user_id=user["user_id"])
        return _ok(result)

    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("Ingest pipeline failed: %s", exc)
        return _err("Internal error", 500)


@app.route(route="ingest-eac", methods=["POST"])
def ingest_eac(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/ingest-eac — Upload EAC variance Excel."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    try:
        file_bytes: bytes = b""
        files = req.files
        if files and "file" in files:
            file_bytes = files["file"].read()
        else:
            file_bytes = req.get_body()

        if not file_bytes:
            return _err("No file provided", 400)

        result = run_ingest_eac(file_bytes, user_id=user["user_id"])
        return _ok(result)

    except Exception as exc:
        logger.exception("EAC Ingest failed: %s", exc)
        return _err("Internal error", 500)


@app.route(route="validate", methods=["POST"])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/validate — Validate a single narrative."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    narrative    = body.get("narrative", "").strip()
    project_name = body.get("project_name", "").strip()
    period       = body.get("period", None)
    top_k        = int(body.get("top_k", 5))

    if not narrative:
        return _err("'narrative' field is required", 400)
    if not project_name:
        return _err("'project_name' field is required", 400)

    try:
        result = run_validate(
            narrative=narrative,
            project_name=project_name,
            period=period,
            top_k=top_k,
            user_id=user["user_id"],
        )
        return func.HttpResponse(json.dumps(result, indent=2), status_code=200, mimetype="application/json")
    except Exception as exc:
        logger.exception("Validation pipeline failed: %s", exc)
        return _err(f"Validation failed: {exc}", 500)


@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/chat — Conversational RAG with session memory."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    question   = body.get("question", "").strip()
    session_id = body.get("session_id") or None
    history    = body.get("history", [])

    if not question:
        return _err("'question' field is required", 400)

    try:
        result = run_chat(
            question=question,
            session_id=session_id,
            history=history,
            user_id=user["user_id"],
        )
        return func.HttpResponse(json.dumps(result, indent=2), status_code=200, mimetype="application/json")
    except Exception as exc:
        logger.exception("Chat pipeline failed: %s", exc)
        return _err("Internal error", 500)


@app.route(route="list-projects", methods=["POST"])
def list_projects(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/list-projects — Extract project list from Excel (no DB write)."""
    try:
        require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

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
            return _err("No file provided.", 400)

        result = list_projects_from_bytes(file_bytes, filename=filename)
        return _ok(result)

    except Exception as exc:
        logger.exception("List projects failed: %s", exc)
        return _err("Internal error", 500)


@app.route(route="search-projects", methods=["GET"])
def search_projects_route(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/search-projects?q=<term>&limit=20 — Search user's indexed projects."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    query = req.params.get("q", "").strip()
    if not query:
        return _ok({"projects": []})

    try:
        limit   = min(int(req.params.get("limit", 20)), 50)
        results = search_projects(query, user_id=user["user_id"], limit=limit)
        return _ok({"projects": results})
    except Exception as exc:
        logger.exception("search-projects failed: %s", exc)
        return _err("Internal error", 500)


# ══════════════════════════════════════════════════════════════════════════════
# SHAREPOINT ROUTES  (require valid JWT)
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="sharepoint/files", methods=["GET"])
def sharepoint_files(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/sharepoint/files — List Excel files from configured SharePoint drive folder."""
    try:
        require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    try:
        files = sp_list_files()
        return _ok({"files": files})
    except ValueError as exc:
        return _err(str(exc), 500)
    except Exception as exc:
        logger.exception("sharepoint/files failed: %s", exc)
        return _err("Failed to list SharePoint files.", 500)


@app.route(route="sharepoint/list-projects", methods=["POST"])
def sharepoint_list_projects(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/sharepoint/list-projects — Download SharePoint file, extract project list."""
    try:
        require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

    try:
        body = req.get_json()
    except ValueError:
        return _err("Request body must be valid JSON", 400)

    file_id = body.get("file_id", "").strip()
    if not file_id:
        return _err("'file_id' is required", 400)

    try:
        file_bytes = sp_download_file(file_id)
        result     = list_projects_from_bytes(file_bytes)
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("sharepoint/list-projects failed: %s", exc)
        return _err("Failed to load projects from SharePoint file.", 500)


@app.route(route="batch-validate", methods=["POST"])
def batch_validate(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/batch-validate — Validate every narrative in an Excel file."""
    try:
        user = require_auth(req)
    except PermissionError as exc:
        return _err(str(exc), 401)

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
            return _err("No file provided.", 400)

        top_k  = int(req.params.get("top_k", 5))
        result = run_batch_validate(
            file_bytes, filename=filename, top_k=top_k, user_id=user["user_id"]
        )
        return _ok(result)

    except Exception as exc:
        logger.exception("Batch validate failed: %s", exc)
        return _err("Internal error", 500)