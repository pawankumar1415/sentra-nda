"""
rag_function/conversation.py — PostgreSQL-backed conversation session management.

Provides three public functions used by chat.py to persist and retrieve
conversation history across requests:

    create_session(metadata)        → session_id (UUID string)
    load_history(session_id, n)     → [{"role": ..., "content": ...}, ...]
    save_turn(session_id, user, assistant, metadata)  → None

Design notes
────────────
- The server owns session state.  The client only needs to store and send back
  the opaque `session_id` UUID (kept in localStorage on the frontend).
- Full history is stored permanently in `chat_messages` for audit/compliance.
  Only the most recent MAX_HISTORY_MESSAGES messages are loaded per LLM call to
  control token cost.
- History is fetched with a single index scan (ORDER BY id DESC LIMIT n) then
  reversed in Python — one round-trip, no pagination.
- Both user and assistant messages for a turn are inserted in one multi-row
  INSERT so a partial write never leaves an orphaned user message.

Required tables (created by db.py ensure_schema):
    chat_sessions  (session_id UUID PK, created_at, updated_at, metadata JSONB)
    chat_messages  (id BIGSERIAL PK, session_id UUID FK, role TEXT, content TEXT,
                    created_at TIMESTAMPTZ, metadata JSONB)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from db import DBConnection

logger = logging.getLogger(__name__)

# Maximum number of messages (user + assistant combined) loaded per LLM call.
# Keeping last 20 messages (10 exchanges) balances context richness vs token cost.
# The full history is always retained in the DB for audit purposes.
MAX_HISTORY_MESSAGES: int = 20


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_session(metadata: Optional[Dict[str, Any]] = None, user_id: Optional[str] = None) -> str:
    """
    Create a new chat session in the database and return its UUID.

    Args:
        metadata: Optional dict stored as JSONB on the session row.
                  Useful for tagging the originating project, user-agent, etc.
        user_id:  UUID of the authenticated user — scopes the session.

    Returns:
        session_id as a string (UUID v4).

    Example:
        session_id = create_session({"source": "chat_view"}, user_id="abc-123")
    """
    session_id = str(uuid.uuid4())
    meta_json  = json.dumps(metadata or {})

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, metadata, user_id)
                VALUES (%s, %s, %s)
                """,
                (session_id, meta_json, user_id),
            )

    logger.info("Created chat session %s (user_id=%s)", session_id, user_id)
    return session_id


def session_exists(session_id: str) -> bool:
    """
    Return True if the given session_id exists in the database.

    Used by the chat route to validate that the client-supplied session_id
    is legitimate before loading history, preventing accidental cross-session
    data leakage from a mis-typed or stale ID.

    Args:
        session_id: UUID string to look up.
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            return cur.fetchone() is not None


def load_history(
    session_id: str,
    max_messages: int = MAX_HISTORY_MESSAGES,
) -> List[Dict[str, str]]:
    """
    Load the most recent `max_messages` messages for a session, ordered
    oldest-first so they can be passed directly to the LLM messages array.

    Strategy: fetch newest-N by descending id, then reverse in Python.
    This is a single index scan on idx_chat_messages_session — no pagination.

    Args:
        session_id:   UUID string of the session to load.
        max_messages: Maximum messages to return.  Defaults to MAX_HISTORY_MESSAGES.

    Returns:
        List of {"role": "user"|"assistant", "content": "..."} dicts,
        ordered from oldest to newest (chronological order for the LLM).

    Example:
        history = load_history("550e8400-e29b-41d4-a716-446655440000")
        # → [{"role": "user", "content": "..."}, {"role": "assistant", ...}, ...]
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content
                FROM (
                    SELECT id, role, content
                    FROM   chat_messages
                    WHERE  session_id = %s
                    ORDER  BY id DESC
                    LIMIT  %s
                ) recent
                ORDER BY id ASC
                """,
                (session_id, max_messages),
            )
            rows = cur.fetchall()

    history = [{"role": row[0], "content": row[1]} for row in rows]
    logger.debug(
        "Loaded %d messages for session %s (limit=%d)",
        len(history), session_id, max_messages,
    )
    return history


def save_turn(
    session_id: str,
    user_message: str,
    assistant_message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Persist one user/assistant exchange as two rows in chat_messages, then
    bump the session's updated_at timestamp.

    Both rows are inserted in a single multi-row INSERT so a partial failure
    never leaves an orphaned user message without its corresponding reply.

    Args:
        session_id:        UUID string of the session.
        user_message:      The user's question/input for this turn.
        assistant_message: The assistant's reply for this turn.
        metadata:          Optional dict attached to the assistant message row.
                           Useful for storing detected intent, projects_detected,
                           context_length, etc. for debugging/auditing.

    Example:
        save_turn(
            session_id="550e8400...",
            user_message="What is the RAG status of BEPPS2?",
            assistant_message="In P07, BEPPS2 has a RAG status of Amber...",
            metadata={"intent": "project_query", "projects_detected": ["BEPPS2"]},
        )
    """
    meta_json = json.dumps(metadata or {})

    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Insert both messages in one statement — atomic, single round-trip
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata)
                VALUES
                    (%s, 'user',      %s, '{}'),
                    (%s, 'assistant', %s, %s)
                """,
                (
                    session_id, user_message,
                    session_id, assistant_message, meta_json,
                ),
            )
            # Bump updated_at so we can sort/filter sessions by last activity
            cur.execute(
                "UPDATE chat_sessions SET updated_at = NOW() WHERE session_id = %s",
                (session_id,),
            )

    logger.info(
        "Saved turn for session %s (user=%d chars, assistant=%d chars)",
        session_id, len(user_message), len(assistant_message),
    )


def delete_session(session_id: str) -> bool:
    """
    Hard-delete a session and all its messages (CASCADE).

    Returns True if a row was deleted, False if the session_id was not found.
    Intended for GDPR-style "forget me" flows or test cleanup.

    Args:
        session_id: UUID string of the session to delete.
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            deleted = cur.rowcount > 0

    if deleted:
        logger.info("Deleted chat session %s and all its messages", session_id)
    else:
        logger.warning("delete_session: session %s not found", session_id)

    return deleted
