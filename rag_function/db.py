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

-- ── Users (auth) ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    is_admin      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    indexed_at              TIMESTAMPTZ DEFAULT NOW(),
    user_id                 UUID REFERENCES users(id)
);

-- ── EAC variance lookup table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nda_eac_variance (
    project_name            TEXT        NOT NULL,
    user_id                 UUID        NOT NULL REFERENCES users(id),
    period_short_name       TEXT,
    eac_variance            DOUBLE PRECISION,
    schedule_variance_days  INTEGER,
    flag                    TEXT,
    summary_text            TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (project_name, user_id)
);

-- ── Conversation memory: sessions ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB       NOT NULL DEFAULT '{{}}',
    user_id     UUID        REFERENCES users(id)
);

-- ── Conversation memory: messages ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB       NOT NULL DEFAULT '{{}}'
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id ASC);

-- ── pgvector IVFFlat index on project embeddings ──────────────────────────────
CREATE INDEX IF NOT EXISTS nda_projects_embedding_idx
    ON nda_projects
    USING ivfflat ((embedding::halfvec({_EMBEDDING_DIMS})) halfvec_cosine_ops)
    WITH (lists = 50);
"""

# Run on every cold-start to migrate existing deployments that pre-date auth.
_MIGRATION_SQL = """
ALTER TABLE nda_projects  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);

-- nda_eac_variance changed from PK(project_name) to PK(project_name, user_id).
-- If the old schema is present (no user_id column), drop and let CREATE TABLE rebuild it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'nda_eac_variance' AND column_name = 'user_id'
    ) THEN
        DROP TABLE IF EXISTS nda_eac_variance;
    END IF;
END $$;
"""


def search_projects(query: str, user_id: str, limit: int = 20) -> list:
    """
    Search indexed project names in nda_projects scoped to the given user.

    Args:
        query:   Partial project name to search for (ILIKE '%query%').
        user_id: UUID of the authenticated user — only their projects are returned.
        limit:   Maximum number of distinct projects to return.

    Returns:
        List of dicts: [{project_name, period_short_name, narrative_text}]
    """
    sql = """
        SELECT DISTINCT ON (project_name)
               project_name,
               period_short_name,
               narrative_text
        FROM   nda_projects
        WHERE  project_name ILIKE %s
          AND  user_id = %s
        ORDER  BY project_name, indexed_at DESC
        LIMIT  %s
    """
    pattern = f"%{query}%"
    results = []
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (pattern, user_id, limit))
            for row in cur.fetchall():
                results.append({
                    "project_name":      row[0],
                    "period_short_name": row[1] or "",
                    "narrative_text":    row[2] or "",
                })
    return results


def ensure_schema() -> None:
    """Create all tables/indexes if not present, then run ALTER TABLE migrations. Idempotent."""
    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                cur.execute(_MIGRATION_SQL)
        logger.info("DB schema verified / migrated")
    except Exception as exc:
        logger.error("Failed to ensure DB schema: %s", exc)
        raise
