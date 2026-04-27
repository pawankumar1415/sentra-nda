"""
PGVector-backed helpers for parallel agent routes.

These functions keep the public HTTP contracts used by agent/function_app.py
but swap Azure AI Search / Foundry execution for PostgreSQL pgvector plus
Azure OpenAI chat and embeddings.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import psycopg2
from openai import AzureOpenAI
from pgvector.psycopg2 import register_vector
from psycopg2 import pool as pg_pool

from guidance_loader import get_guidance_text

logger = logging.getLogger(__name__)

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_embedding_client: Optional[AzureOpenAI] = None
_chat_client: Optional[AzureOpenAI] = None

_EMBEDDING_DIMS = int(os.environ.get("AZURE_OPENAI_EMBEDDING_DIMS", "3072"))
_PERIOD_RE = re.compile(r"\b(P\d{2})\b", re.IGNORECASE)
_RAG_VALUES = {"r", "a", "g", "-", "n/a", "tbd"}


def _get_password() -> str:
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if "@" in user or not password:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token(
            "https://ossrdbms-aad.database.windows.net/.default"
        )
        return token.token
    return password


def _dsn() -> str:
    return (
        f"host={os.environ['POSTGRES_HOST']} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_USER']} "
        f"password={_get_password()} "
        f"sslmode={os.environ.get('POSTGRES_SSL', 'require')}"
    )


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=_dsn())
    return _pool


class DBConnection:
    def __enter__(self):
        self._pool = _get_pool()
        self.conn = self._pool.getconn()
        register_vector(self.conn)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self._pool.putconn(self.conn)
        return False


_SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

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
    user_id                 UUID
);

CREATE TABLE IF NOT EXISTS nda_eac_variance (
    project_name            TEXT PRIMARY KEY,
    period_short_name       TEXT,
    eac_variance            DOUBLE PRECISION,
    schedule_variance_days  INTEGER,
    flag                    TEXT,
    summary_text            TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    user_id                 UUID
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB       NOT NULL DEFAULT '{{}}',
    user_id     UUID
);

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

CREATE INDEX IF NOT EXISTS nda_projects_embedding_idx
    ON nda_projects
    USING ivfflat ((embedding::halfvec({_EMBEDDING_DIMS})) halfvec_cosine_ops)
    WITH (lists = 50);
"""


def ensure_pgvector_schema() -> None:
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)


def _openai_client() -> AzureOpenAI:
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
    return _embedding_client


def _gpt_client() -> AzureOpenAI:
    global _chat_client
    if _chat_client is None:
        _chat_client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
    return _chat_client


def _embedding_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")


def _chat_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.1-chat")


def embed(text: str) -> List[float]:
    resp = _openai_client().embeddings.create(
        model=_embedding_deployment(),
        input=text.replace("\n", " "),
    )
    return resp.data[0].embedding


def embed_batch(texts: List[str], batch_size: int = 16) -> List[List[float]]:
    results: List[List[float]] = []
    client = _openai_client()
    model = _embedding_deployment()
    for i in range(0, len(texts), batch_size):
        batch = [t.replace("\n", " ") for t in texts[i : i + batch_size]]
        resp = client.embeddings.create(model=model, input=batch)
        results.extend([item.embedding for item in sorted(resp.data, key=lambda x: x.index)])
    return results


def _safe(value: Any, default: str = "") -> str:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _extract_period(filename: str, df: pd.DataFrame) -> str:
    m = _PERIOD_RE.search(filename or "")
    if m:
        return m.group(1).upper()
    for i in range(min(6, len(df))):
        for j in range(min(10, len(df.columns))):
            m = _PERIOD_RE.search(_safe(df.iloc[i, j]))
            if m:
                return m.group(1).upper()
    return "UNKNOWN"


def parse_excel(file_bytes: bytes, filename: str = "") -> Tuple[str, List[Dict]]:
    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_name = next((s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None)
    if not sheet_name:
        raise ValueError(f"Sheet '5a)NDA MPPR' not found. Available: {xl.sheet_names}")

    df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    period = _extract_period(filename, df)
    projects: List[Dict] = []

    for i in range(6, len(df)):
        row = df.iloc[i]
        col0 = _safe(row.iloc[0]) if len(row) > 0 else ""
        col1 = _safe(row.iloc[1]) if len(row) > 1 else ""
        col3 = _safe(row.iloc[3]) if len(row) > 3 else ""

        if not (col0 == "" and col1 and len(col1) >= 3 and col3.lower() in _RAG_VALUES):
            continue

        numeric_cols: Dict[int, float] = {}
        for ci in range(4, len(row)):
            val = row.iloc[ci]
            if isinstance(val, (int, float)) and not pd.isna(val):
                numeric_cols[ci] = float(val)

        narrative_text = ""
        for j in range(i + 1, min(i + 4, len(df))):
            nrow = df.iloc[j]
            nc0 = _safe(nrow.iloc[0]) if len(nrow) > 0 else ""
            nc1 = _safe(nrow.iloc[1]) if len(nrow) > 1 else ""
            if nc0 and nc1 and len(nc1) > 40:
                narrative_text = nc1
                break

        eac_total = _safe_float(numeric_cols.get(12, numeric_cols.get(13, 0.0)))
        eac_variance = _safe_float(numeric_cols.get(13, numeric_cols.get(14, 0.0)))
        sched_days = _safe_int(numeric_cols.get(15, numeric_cols.get(16, 0)))
        project_name = col1
        dca_rag = col3.upper()
        raw_content = (
            f"Project: {project_name} | DCA RAG: {dca_rag} | "
            f"EAC (GBP m): {eac_total:.3f} | EAC Variance (GBP m): {eac_variance:.3f} | "
            f"Schedule Variance (days): {sched_days} | Narrative: {narrative_text}"
        )

        projects.append({
            "project_id": f"{period}|{project_name}",
            "project_name": project_name,
            "period_short_name": period,
            "rag_status": dca_rag,
            "dca_rag_status": dca_rag,
            "capability_capacity_rag": "",
            "eac_total": eac_total,
            "eac_variance": eac_variance,
            "schedule_variance_days": sched_days,
            "narrative_text": narrative_text,
            "raw_content": raw_content,
        })

    return period, projects


def list_projects_from_bytes(file_bytes: bytes, filename: str = "") -> Dict:
    period, projects = parse_excel(file_bytes, filename=filename)
    return {
        "period": period,
        "projects": [
            {"project_name": p["project_name"], "narrative_text": p["narrative_text"]}
            for p in projects
        ],
    }


def ingest_mppr_pgvector(file_bytes: bytes, filename: str = "", user_id: str = "") -> Dict:
    ensure_pgvector_schema()
    period, projects = parse_excel(file_bytes, filename=filename)
    if not projects:
        return {
            "message": "No valid projects found in the uploaded file.",
            "reporting_period": period,
            "projects_extracted": 0,
            "documents_indexed": 0,
            "documents_failed": 0,
        }

    vectors = embed_batch([p["raw_content"] for p in projects])
    upsert_sql = """
        INSERT INTO nda_projects (
            project_id, project_name, period_short_name,
            rag_status, dca_rag_status, capability_capacity_rag,
            eac_total, eac_variance, schedule_variance_days,
            narrative_text, raw_content, embedding, indexed_at, user_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s
        )
        ON CONFLICT (project_id) DO UPDATE SET
            project_name            = EXCLUDED.project_name,
            period_short_name       = EXCLUDED.period_short_name,
            rag_status              = EXCLUDED.rag_status,
            dca_rag_status          = EXCLUDED.dca_rag_status,
            capability_capacity_rag = EXCLUDED.capability_capacity_rag,
            eac_total               = EXCLUDED.eac_total,
            eac_variance            = EXCLUDED.eac_variance,
            schedule_variance_days  = EXCLUDED.schedule_variance_days,
            narrative_text          = EXCLUDED.narrative_text,
            raw_content             = EXCLUDED.raw_content,
            embedding               = EXCLUDED.embedding,
            indexed_at              = NOW(),
            user_id                 = EXCLUDED.user_id;
    """

    failed = 0
    with DBConnection() as conn:
        with conn.cursor() as cur:
            for project, vector in zip(projects, vectors):
                try:
                    cur.execute(upsert_sql, (
                        project["project_id"], project["project_name"],
                        project["period_short_name"], project["rag_status"],
                        project["dca_rag_status"], project["capability_capacity_rag"],
                        project["eac_total"], project["eac_variance"],
                        project["schedule_variance_days"], project["narrative_text"],
                        project["raw_content"], vector, user_id or None,
                    ))
                except Exception:
                    failed += 1
                    logger.exception("Failed to upsert project %s", project["project_name"])

    return {
        "message": "Processing complete.",
        "reporting_period": period,
        "projects_extracted": len(projects),
        "documents_indexed": len(projects) - failed,
        "documents_failed": failed,
    }


def parse_eac_excel(file_bytes: bytes) -> List[Dict]:
    df = pd.read_excel(io.BytesIO(file_bytes))
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("\n", "") for c in df.columns]

    name_col = next((c for c in df.columns if "project" in c and "name" in c), None)
    if not name_col:
        name_col = next((c for c in df.columns if "project" in c or "programme" in c or "title" in c), None)
    if not name_col:
        raise ValueError(f"Could not find project name column. Available columns: {list(df.columns)}")

    period_col = next((c for c in df.columns if "period" in c), None)
    eac_col = next((c for c in df.columns if "eac" in c and "variance" in c), None)
    sched_col = next((c for c in df.columns if "variance" in c and "day" in c), None)
    if not sched_col:
        sched_col = next((c for c in df.columns if "schedule" in c and "variance" in c), None)

    rows = []
    for _, row in df.iterrows():
        name = str(row[name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        period = str(row[period_col]).strip() if period_col and not pd.isna(row[period_col]) else ""
        eac_m = _safe_float(row[eac_col], 0.0) if eac_col else 0.0
        sched = _safe_int(row[sched_col], 0) if sched_col else 0
        eac_pounds = eac_m * 1_000_000
        abs_var = abs(eac_pounds)
        if abs_var >= 500_000:
            flag = "major"
        elif abs_var >= 100_000:
            flag = "material"
        elif abs_var >= 50_000:
            flag = "minor"
        else:
            flag = "none"
        rows.append({
            "project_name": name,
            "period_short_name": period,
            "eac_variance": eac_pounds,
            "schedule_variance_days": sched,
            "flag": flag,
            "summary_text": f"EAC variance: GBP {eac_m:.2f}m ({flag}). Schedule variance: {sched} days.",
        })
    return rows


def ingest_eac_pgvector(file_bytes: bytes, user_id: str = "") -> Dict:
    ensure_pgvector_schema()
    rows = parse_eac_excel(file_bytes)
    upsert_sql = """
        INSERT INTO nda_eac_variance (
            project_name, period_short_name, eac_variance,
            schedule_variance_days, flag, summary_text, updated_at, user_id
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (project_name) DO UPDATE SET
            period_short_name      = EXCLUDED.period_short_name,
            eac_variance           = EXCLUDED.eac_variance,
            schedule_variance_days = EXCLUDED.schedule_variance_days,
            flag                   = EXCLUDED.flag,
            summary_text           = EXCLUDED.summary_text,
            updated_at             = NOW(),
            user_id                = EXCLUDED.user_id;
    """
    with DBConnection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(upsert_sql, (
                    row["project_name"], row["period_short_name"], row["eac_variance"],
                    row["schedule_variance_days"], row["flag"], row["summary_text"],
                    user_id or None,
                ))

    return {
        "message": "EAC variance data ingested successfully.",
        "container": "postgresql",
        "blob": "nda_eac_variance",
        "indexed": len(rows),
    }


def search_projects_pgvector(query: str, limit: int = 20) -> List[Dict]:
    ensure_pgvector_schema()
    sql = """
        SELECT DISTINCT ON (project_name)
               project_name, period_short_name, narrative_text
        FROM nda_projects
        WHERE project_name ILIKE %s
        ORDER BY project_name, indexed_at DESC
        LIMIT %s
    """
    projects = []
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (f"%{query}%", limit))
            for row in cur.fetchall():
                projects.append({
                    "project_name": row[0],
                    "period_short_name": row[1] or "",
                    "narrative_text": row[2] or "",
                })
    return projects


def _retrieve(query_vector: List[float], project_name: Optional[str], top_k: int = 5) -> List[Dict]:
    if project_name:
        sql = """
            SELECT project_name, period_short_name, raw_content, narrative_text,
                   1 - (embedding <=> %s::vector) AS score
            FROM nda_projects
            WHERE lower(project_name) LIKE lower(%s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """
        params = (query_vector, f"%{project_name}%", query_vector, top_k)
    else:
        sql = """
            SELECT project_name, period_short_name, raw_content, narrative_text,
                   1 - (embedding <=> %s::vector) AS score
            FROM nda_projects
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """
        params = (query_vector, query_vector, top_k)

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [
        {
            "project_name": r[0],
            "period_short_name": r[1],
            "raw_content": r[2],
            "narrative_text": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]


def _load_eac_data(project_name: str, period: Optional[str]) -> Dict[str, Any]:
    default = {
        "eac_variance": 0.0,
        "schedule_days": 0,
        "flag": "no_data",
        "summary_text": "EAC variance data not available.",
    }
    sql = """
        SELECT eac_variance, schedule_variance_days, flag, summary_text
        FROM nda_eac_variance
        WHERE lower(project_name) LIKE lower(%s)
    """
    params: List[Any] = [f"%{project_name}%"]
    if period:
        sql += " AND lower(period_short_name) = lower(%s)"
        params.append(period)
    sql += " LIMIT 1;"

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            if not row and period:
                cur.execute(
                    """
                    SELECT eac_variance, schedule_variance_days, flag, summary_text
                    FROM nda_eac_variance
                    WHERE lower(project_name) LIKE lower(%s)
                    ORDER BY period_short_name DESC LIMIT 1;
                    """,
                    (f"%{project_name}%",),
                )
                row = cur.fetchone()

    if not row:
        return default
    return {
        "eac_variance": row[0],
        "schedule_days": row[1],
        "flag": row[2],
        "summary_text": row[3],
    }


_VALIDATE_SYSTEM_TEMPLATE = """You are the NDA Narrative Validation Agent. Validate project narrative text using two layers.

LAYER 1 - GUIDANCE & STRUCTURE:
{guidance}

LAYER 2 - DATA VALIDATION:
- EAC movement >= GBP 0.1m (flag=material/major) must be explained in narrative
- Schedule slip (positive days) must be mentioned
- RAG change must be acknowledged with reason

Return JSON only with:
{{
  "layer1": {{"compliance_score": <int 0-10>, "issues": [], "passed": []}},
  "layer2": {{"eac_explained": <true|false|"not_applicable">, "schedule_explained": <true|false|"not_applicable">, "data_flag": <"none"|"minor"|"material"|"major">, "issues": []}},
  "rewritten_narrative": <string>,
  "overall_verdict": <"PASS"|"PASS_WITH_WARNINGS"|"FAIL">
}}"""


def validate_narrative_pgvector(
    project_name: str,
    narrative_text: str,
    period: str = "",
    conversation_id: Optional[str] = None,
    user_scope: Optional[str] = None,
) -> Dict:
    ensure_pgvector_schema()
    conv_id = conversation_id or str(uuid.uuid4())
    query_vector = embed(narrative_text)
    chunks = _retrieve(query_vector, project_name, top_k=5)
    eac_data = _load_eac_data(project_name, period)
    chunks_text = "\n\n".join(
        f"[Retrieved context - {c['project_name']} / {c['period_short_name']} "
        f"(similarity: {c['score']:.2f})]:\n{c['raw_content']}"
        for c in chunks
    )
    guidance_text, guidance_source = get_guidance_text()
    user_message = f"""PROJECT: {project_name}

RETRIEVED DATA CONTEXT:
{chunks_text}

EAC / SCHEDULE MOVEMENT DATA:
{eac_data['summary_text']}
EAC variance flag: {eac_data['flag']}

NARRATIVE TO VALIDATE:
{narrative_text}

Validate this narrative. Return JSON only."""

    resp = _gpt_client().chat.completions.create(
        model=_chat_deployment(),
        messages=[
            {"role": "system", "content": _VALIDATE_SYSTEM_TEMPLATE.format(guidance=guidance_text)},
            {"role": "user", "content": user_message},
        ],
        temperature=1,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        result = json.loads(raw)
        score = result.get("layer1", {}).get("compliance_score", 0)
        if score >= 8:
            result["overall_verdict"] = "PASS"
        elif score >= 6:
            result["overall_verdict"] = "PASS_WITH_WARNINGS"
        else:
            result["overall_verdict"] = "FAIL"
    except json.JSONDecodeError:
        result = {"raw_response": raw, "parse_error": True}

    result["_meta"] = {
        "project_name": project_name,
        "period": period,
        "chunks_used": len(chunks),
        "eac_flag": eac_data["flag"],
        "eac_variance_m": round((eac_data["eac_variance"] or 0) / 1_000_000, 3),
        "schedule_days": eac_data["schedule_days"],
        "guidance_source": guidance_source,
    }
    return {
        "conversation_id": conv_id,
        "is_new_conversation": conversation_id is None,
        "validation_result": json.dumps(result, ensure_ascii=False, indent=2),
    }


def batch_validate_pgvector(file_bytes: bytes, filename: str = "") -> Dict:
    period, projects = parse_excel(file_bytes, filename=filename)
    results = []
    for project in projects:
        project_name = project["project_name"]
        narrative = (project.get("narrative_text") or "").strip()
        if not narrative:
            results.append({
                "project_name": project_name,
                "status": "skipped",
                "validation_result": "No narrative text in the Excel file for this project.",
                "conversation_id": None,
            })
            continue
        try:
            val = validate_narrative_pgvector(project_name, narrative, period)
            results.append({
                "project_name": project_name,
                "status": "ok",
                "validation_result": val["validation_result"],
                "conversation_id": val["conversation_id"],
            })
        except Exception as exc:
            logger.exception("PGVector batch validation failed for %s", project_name)
            results.append({
                "project_name": project_name,
                "status": "error",
                "validation_result": f"Validation error: {exc}",
                "conversation_id": None,
            })
    return {"status": "ok", "period": period, "total": len(results), "results": results}


def _session_exists(session_id: str) -> bool:
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM chat_sessions WHERE session_id = %s", (session_id,))
            return cur.fetchone() is not None


def _create_session() -> str:
    sid = str(uuid.uuid4())
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chat_sessions (session_id, metadata) VALUES (%s, %s)",
                (sid, json.dumps({"source": "agent_pgvector_chat"})),
            )
    return sid


def _load_history(session_id: str, max_messages: int = 20) -> List[Dict[str, str]]:
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content
                FROM (
                    SELECT id, role, content
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                ) recent
                ORDER BY id ASC
                """,
                (session_id, max_messages),
            )
            return [{"role": r[0], "content": r[1]} for r in cur.fetchall()]


def _save_turn(session_id: str, question: str, answer: str, metadata: Dict) -> None:
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata)
                VALUES (%s, 'user', %s, '{}'), (%s, 'assistant', %s, %s)
                """,
                (session_id, question, session_id, answer, json.dumps(metadata)),
            )
            cur.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE session_id = %s", (session_id,))


def _vector_chat_context(question: str, top_k: int = 5) -> str:
    query_vector = embed(question)
    rows = _retrieve(query_vector, None, top_k=top_k)
    if not rows:
        return "No matching narrative data found."
    return "RETRIEVED PROJECT CONTEXT:\n" + "\n\n".join(
        f"--- {r['project_name']} ({r['period_short_name']}) [Score: {r['score']:.2f}] ---\n{r['raw_content']}"
        for r in rows
    )


_CHAT_SYSTEM = """You are the Sentra RAG Assistant, an expert AI analyzing the NDA portfolio.
Answer based strictly on the provided Context Data. If the context does not contain the answer, say so.
Return raw compact HTML only. Do not use Markdown or code fences. Do not use <br> tags."""


def chat_pgvector(question: str, session_id: Optional[str] = None) -> Dict:
    ensure_pgvector_schema()
    is_new = False
    if session_id and _session_exists(session_id):
        history = _load_history(session_id)
    else:
        session_id = _create_session()
        history = []
        is_new = True

    context = _vector_chat_context(question)
    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": f"CONTEXT DATA:\n{context}\n\nUSER QUESTION:\n{question}"})

    resp = _gpt_client().chat.completions.create(
        model=_chat_deployment(),
        messages=messages,
    )
    answer = resp.choices[0].message.content or ""
    answer = re.sub(r"<br\s*/?>", "", answer, flags=re.IGNORECASE)
    answer = re.sub(r">\s+<", "><", answer)
    meta = {
        "intent": "pgvector_search",
        "projects_detected": [],
        "context_length": len(context),
        "is_new_session": is_new,
    }
    _save_turn(session_id, question, answer, meta)
    return {"answer": answer, "session_id": session_id, "meta": meta}
