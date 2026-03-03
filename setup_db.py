"""
setup_db.py — Create the pgvector extension, nda_projects table, and indexes.

Run once against your Azure PostgreSQL Flexible Server:
    python setup_db.py

Reads credentials from rag_function/local.settings.json automatically.

Authentication:
    - If POSTGRES_USER contains '@' (Entra ID / AAD user email), fetches a
      bearer token via DefaultAzureCredential. Run 'az login' first.
    - Otherwise uses POSTGRES_PASSWORD directly (standard password auth).
"""

import json
import pathlib
import sys
import os

# ── Load credentials from local.settings.json ─────────────────────────────────
settings_path = pathlib.Path(__file__).parent / "rag_function" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        values = json.load(f).get("Values", {})
    for k, v in values.items():
        os.environ.setdefault(k, v)
    print(f"✅ Loaded credentials from {settings_path}")
else:
    print(f"⚠️  {settings_path} not found — using existing environment variables")

import psycopg2
from pgvector.psycopg2 import register_vector


# ── Auth: Entra ID token OR plain password ────────────────────────────────────
def _get_password() -> str:
    """
    Detects auth mode from POSTGRES_USER:
    - Email address (@) → Entra ID bearer token via DefaultAzureCredential
    - Otherwise         → plain POSTGRES_PASSWORD
    """
    user = os.environ.get("POSTGRES_USER", "")
    if "@" in user:
        try:
            from azure.identity import DefaultAzureCredential
            print("  Auth mode: Entra ID (fetching token via DefaultAzureCredential)")
            cred  = DefaultAzureCredential()
            token = cred.get_token("https://ossrdbms-aad.database.windows.net/.default")
            print("  ✅ Entra ID token obtained")
            return token.token
        except Exception as e:
            print(f"  ❌ Entra ID token fetch failed: {e}")
            print("     → Make sure 'azure-identity' is installed and 'az login' has been run")
            sys.exit(1)
    else:
        print("  Auth mode: password")
        return os.environ["POSTGRES_PASSWORD"]


# ── Connection ─────────────────────────────────────────────────────────────────
def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host    =os.environ["POSTGRES_HOST"],
        port    =int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname  =os.environ["POSTGRES_DB"],
        user    =os.environ["POSTGRES_USER"],
        password=_get_password(),
        sslmode =os.environ.get("POSTGRES_SSL", "require"),
    )


# ── DDL Steps ─────────────────────────────────────────────────────────────────
STEPS = [
    (
        "Enable pgvector extension",
        "CREATE EXTENSION IF NOT EXISTS vector;",
    ),
    (
        "Create nda_projects table",
        """
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
            embedding               vector(1536),
            indexed_at              TIMESTAMPTZ DEFAULT NOW()
        );
        """,
    ),
    (
        "Create IVFFlat cosine index on embedding",
        """
        CREATE INDEX IF NOT EXISTS nda_projects_embedding_idx
            ON nda_projects
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 50);
        """,
    ),
    (
        "Create period index for fast filtering",
        """
        CREATE INDEX IF NOT EXISTS nda_projects_period_idx
            ON nda_projects (period_short_name);
        """,
    ),
]


def main():
    print("\n" + "=" * 55)
    print("NDA Agent — PostgreSQL Schema Setup")
    print("=" * 55)
    print(f"  Host : {os.environ.get('POSTGRES_HOST', '(not set)')}")
    print(f"  DB   : {os.environ.get('POSTGRES_DB',   '(not set)')}")
    print(f"  User : {os.environ.get('POSTGRES_USER', '(not set)')}")
    print()

    try:
        conn = get_conn()
        register_vector(conn)
        print("✅ Connected to PostgreSQL\n")
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        sys.exit(1)

    errors = 0
    with conn:
        with conn.cursor() as cur:
            for label, sql in STEPS:
                try:
                    cur.execute(sql)
                    print(f"  ✅ {label}")
                except Exception as e:
                    print(f"  ❌ {label}: {e}")
                    errors += 1

    # ── Verify ────────────────────────────────────────────────────────────────
    print()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'nda_projects'
                ORDER BY ordinal_position;
            """)
            cols = cur.fetchall()
            if cols:
                print("Table 'nda_projects' columns:")
                for col, dtype in cols:
                    print(f"    {col:<30} {dtype}")
            else:
                print("⚠️  Table 'nda_projects' not found.")

            cur.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'nda_projects';
            """)
            idxs = cur.fetchall()
            print(f"\nIndexes ({len(idxs)}):")
            for (name,) in idxs:
                print(f"    {name}")

    conn.close()

    print()
    if errors == 0:
        print("✅ Schema setup complete — ready for ingest.")
    else:
        print(f"⚠️  Setup finished with {errors} error(s). Review above.")


if __name__ == "__main__":
    main()
