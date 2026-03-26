"""
rag_function/auth.py — JWT authentication and user management.

Provides:
    register_user(username, password)  → { token, username, is_admin }
    login_user(username, password)     → { token, username, is_admin }
    require_auth(req)                  → { user_id, username, is_admin }
    require_admin(req)                 → { user_id, username, is_admin }

Password hashing uses PBKDF2-HMAC-SHA256 (stdlib — no extra dependencies).
Tokens are signed JWTs (PyJWT) with 8-hour expiry.

Required environment variable:
    JWT_SECRET   — secret key for signing tokens (set in Function App settings)
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict

import jwt

from db import DBConnection

logger = logging.getLogger(__name__)

_JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
_JWT_EXPIRY_HOURS = 8
_PBKDF2_ITERATIONS = 310_000


# ── Password hashing (stdlib — no bcrypt dependency) ──────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, dk_hex = stored_hash.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_token(user_id: str, username: str, is_admin: bool) -> str:
    payload = {
        "user_id":  user_id,
        "username": username,
        "is_admin": is_admin,
        "exp":      datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


def _decode_token(token: str) -> Dict:
    return jwt.decode(token, _JWT_SECRET, algorithms=["HS256"])


# ── Public auth functions ─────────────────────────────────────────────────────

def register_user(username: str, password: str) -> Dict:
    """
    Create a new user account.
    The very first registered user is automatically made admin.

    Returns { token, username, is_admin }.
    Raises ValueError on validation failures or duplicate username.
    """
    username = username.strip().lower()
    if not username or not password:
        raise ValueError("Username and password are required.")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    password_hash = _hash_password(password)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
            count = cur.fetchone()[0]
            is_admin = (count == 0)  # First user becomes admin

            try:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, is_admin)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (username, password_hash, is_admin),
                )
                user_id = str(cur.fetchone()[0])
            except Exception as exc:
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    raise ValueError("Username already taken. Please choose another.")
                raise

    logger.info("Registered user '%s' (is_admin=%s)", username, is_admin)
    token = _create_token(user_id, username, is_admin)
    return {"token": token, "username": username, "is_admin": is_admin}


def login_user(username: str, password: str) -> Dict:
    """
    Authenticate an existing user.

    Returns { token, username, is_admin }.
    Raises ValueError on bad credentials or deactivated account.
    """
    username = username.strip().lower()

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash, is_admin, is_active FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError("Invalid username or password.")

    user_id, password_hash, is_admin, is_active = str(row[0]), row[1], row[2], row[3]

    if not is_active:
        raise ValueError("Account is deactivated. Please contact an administrator.")

    if not _verify_password(password, password_hash):
        raise ValueError("Invalid username or password.")

    logger.info("User '%s' logged in", username)
    token = _create_token(user_id, username, is_admin)
    return {"token": token, "username": username, "is_admin": is_admin}


def require_auth(req) -> Dict:
    """
    Extract and validate the JWT from the Authorization header.

    Returns { user_id, username, is_admin }.
    Raises PermissionError if the token is missing, expired, or invalid.
    """
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PermissionError("Missing or invalid Authorization header.")

    token = auth_header[7:]
    try:
        payload = _decode_token(token)
        return {
            "user_id":  payload["user_id"],
            "username": payload["username"],
            "is_admin": payload.get("is_admin", False),
        }
    except jwt.ExpiredSignatureError:
        raise PermissionError("Token has expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise PermissionError("Invalid token. Please log in again.")


def require_admin(req) -> Dict:
    """Like require_auth but also enforces is_admin."""
    user = require_auth(req)
    if not user["is_admin"]:
        raise PermissionError("Admin access required.")
    return user


# ── Admin user management helpers ─────────────────────────────────────────────

def list_users() -> list:
    """Return all users with basic stats for the admin panel."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    u.id,
                    u.username,
                    u.is_admin,
                    u.is_active,
                    u.created_at,
                    COUNT(DISTINCT p.project_id)  AS projects_count,
                    COUNT(DISTINCT s.session_id)  AS sessions_count
                FROM users u
                LEFT JOIN nda_projects   p ON p.user_id = u.id
                LEFT JOIN chat_sessions  s ON s.user_id = u.id
                GROUP BY u.id, u.username, u.is_admin, u.is_active, u.created_at
                ORDER BY u.created_at ASC
            """)
            rows = cur.fetchall()

    return [
        {
            "user_id":        str(r[0]),
            "username":       r[1],
            "is_admin":       r[2],
            "is_active":      r[3],
            "created_at":     r[4].isoformat() if r[4] else None,
            "projects_count": r[5],
            "sessions_count": r[6],
        }
        for r in rows
    ]


def update_user(target_user_id: str, requesting_user_id: str, **kwargs) -> Dict:
    """
    Update a user's is_active or is_admin flag.
    Admin cannot deactivate or demote themselves.
    """
    if target_user_id == requesting_user_id:
        if "is_active" in kwargs and not kwargs["is_active"]:
            raise ValueError("You cannot deactivate your own account.")
        if "is_admin" in kwargs and not kwargs["is_admin"]:
            raise ValueError("You cannot remove your own admin rights.")

    allowed = {k: v for k, v in kwargs.items() if k in ("is_active", "is_admin")}
    if not allowed:
        raise ValueError("Nothing to update. Provide is_active or is_admin.")

    set_clause = ", ".join(f"{k} = %s" for k in allowed)
    values = list(allowed.values()) + [target_user_id]

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {set_clause} WHERE id = %s RETURNING username",
                values,
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found.")

    logger.info("Admin updated user %s: %s", target_user_id, allowed)
    return {"updated": True, "user_id": target_user_id, **allowed}


def delete_user(target_user_id: str, requesting_user_id: str) -> Dict:
    """
    Delete a user and all their associated data.
    Admin cannot delete themselves.
    """
    if target_user_id == requesting_user_id:
        raise ValueError("You cannot delete your own account.")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Delete data in dependency order
            cur.execute("DELETE FROM nda_projects     WHERE user_id = %s", (target_user_id,))
            cur.execute("DELETE FROM nda_eac_variance WHERE user_id = %s", (target_user_id,))
            # chat_messages cascade from chat_sessions
            cur.execute("DELETE FROM chat_sessions    WHERE user_id = %s", (target_user_id,))
            cur.execute("DELETE FROM users            WHERE id = %s RETURNING username", (target_user_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found.")
            username = row[0]

    logger.info("Admin deleted user '%s' (%s)", username, target_user_id)
    return {"deleted": True, "user_id": target_user_id, "username": username}