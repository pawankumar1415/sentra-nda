"""
rag_function/db.py — PostgreSQL + pgvector connection pool.

Manages a module-level connection pool so connections are reused
across warm Azure Function invocations (cold start creates the pool once).

Required environment variables:
    POSTGRES_HOST       e.g. myserver.postgres.database.azure.com
    POSTGRES_DB         e.g. nda_agent
    POSTGRES_USER       e.g. nda_admin
    POSTGRES_PASSWORD   secret
    POSTGRES_PORT       default 5432
    POSTGRES_SSL        "require" (default for Azure) | "disable"
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool
from pgvector.psycopg2 import register_vector

logger = logging.getLogger(__name__)

_pool: Optional[pg_pool.ThreadedConnectionPool] = None


def _get_password() -> str:
    """
    Determine PostgreSQL authentication method:
      - If POSTGRES_USER contains '@' (Entra ID email) → fetch Azure AD token
        (locally via 'az login', on Azure via Managed Identity)
      - If POSTGRES_PASSWORD is empty/unset → assume Managed Identity, fetch token
        (for service-principal-style usernames like 'nda-python-backend')
      - Otherwise → use POSTGRES_PASSWORD directly (standard password auth)
    """
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()

    if "@" in user or not password:
        from azure.identity import DefaultAzureCredential
        logger.info("Auth mode: Entra ID / Managed Identity (user=%s)", user)
        cred = DefaultAzureCredential()
        token = cred.get_token("https://ossrdbms-aad.database.windows.net/.default")
        return token.token

    logger.info("Auth mode: password")
    return password


def _get_dsn() -> str:
    """Build the PostgreSQL DSN from environment variables."""
    host     = os.environ["POSTGRES_HOST"]
    db       = os.environ["POSTGRES_DB"]
    user     = os.environ["POSTGRES_USER"]
    port     = os.environ.get("POSTGRES_PORT", "5432")
    ssl      = os.environ.get("POSTGRES_SSL", "require")
    password = _get_password()
    return (
        f"host={host} port={port} dbname={db} "
        f"user={user} password={password} sslmode={ssl}"
    )


def get_pool() -> pg_pool.ThreadedConnectionPool:
    """Return (or lazily create) the module-level connection pool."""
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool")
        _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=_get_dsn())
    return _pool


class DBConnection:
    """
    Context manager that borrows a connection from the pool and returns it.

    Usage:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """

    def __enter__(self):
        self._pool = get_pool()
        self.conn  = self._pool.getconn()
        register_vector(self.conn)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self._pool.putconn(self.conn)
        return False


# ── Schema bootstrap ──────────────────────────────────────────────────────────
_EMBEDDING_DIMS = int(os.environ.get("AZURE_OPENAI_EMBEDDING_DIMS", "3072"))

_SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

-- ── NDA project narratives (vector store) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS nda_projects (
    project_id              TEXT PRIMARY KEY,
    project_name            TEXT,
    period_short_name       TEXT,
    rag_status              TEXT,
    dca_rag_status          TEXT,
    capability_capacity_rag TEXT,
    eac_total               DOUBLE PRECISION,
    eac_variance            DOUBLE PRECISION,
    schedule_variance_days  INTEGER,
    narrative_text          TEXT,
    raw_content             TEXT NOT NULL,
    embedding               vector({_EMBEDDING_DIMS}),
    indexed_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ── EAC variance lookup table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nda_eac_variance (
    project_name            TEXT PRIMARY KEY,
    period_short_name       TEXT,
    eac_variance            DOUBLE PRECISION,
    schedule_variance_days  INTEGER,
    flag                    TEXT,
    summary_text            TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ── Conversation memory: sessions ─────────────────────────────────────────────
-- Each row represents one chat session (one browser tab / one user conversation).
-- session_id is a UUID generated server-side and returned to the client on the
-- first message; the client stores it in localStorage and sends it on subsequent
-- calls so the server can reload history without the client tracking messages.
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Optional free-form metadata (e.g. user-agent, originating project name)
    metadata    JSONB       NOT NULL DEFAULT '{{}}'
);

-- ── Conversation memory: messages ─────────────────────────────────────────────
-- Stores every user/assistant exchange. The full history is kept permanently
-- for audit purposes; only the last N messages are loaded for each LLM call
-- (controlled by MAX_HISTORY_MESSAGES in conversation.py).
CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Stores per-message metadata such as detected intent and projects list
    -- so we can replay/audit exactly what context the LLM received
    metadata    JSONB       NOT NULL DEFAULT '{{}}'
);

-- Index on (session_id, id) so loading history for a session is a single
-- efficient index scan ordered by insertion sequence.
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id ASC);

-- ── pgvector IVFFlat index on project embeddings ──────────────────────────────
CREATE INDEX IF NOT EXISTS nda_projects_embedding_idx
    ON nda_projects
    USING ivfflat ((embedding::halfvec({_EMBEDDING_DIMS})) halfvec_cosine_ops)
    WITH (lists = 50);
"""


def ensure_schema() -> None:
    """Create pgvector extension, table, and index if not present. Idempotent."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
        logger.info("DB schema verified / created")
    except Exception as exc:
        logger.error("Failed to ensure DB schema: %s", exc)
        raise
