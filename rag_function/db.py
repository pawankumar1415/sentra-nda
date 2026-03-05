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
    Return the PostgreSQL password.
    If POSTGRES_USE_ENTRA is set to 'true', fetch an Azure AD bearer token.
    Otherwise use POSTGRES_PASSWORD directly.
    """
    use_entra = os.environ.get("POSTGRES_USE_ENTRA", "").lower() == "true"
    if use_entra:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = cred.get_token("https://ossrdbms-aad.database.windows.net/.default")
        return token.token
    return os.environ["POSTGRES_PASSWORD"]


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

CREATE TABLE IF NOT EXISTS nda_eac_variance (
    project_name            TEXT PRIMARY KEY,
    period_short_name       TEXT,
    eac_variance            DOUBLE PRECISION,
    schedule_variance_days  INTEGER,
    flag                    TEXT,
    summary_text            TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

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
