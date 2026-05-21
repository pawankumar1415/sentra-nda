"""
PGVector-backed helpers for parallel agent routes.

These functions keep the public HTTP contracts used by agent/function_app.py
but swap Azure AI Search / Foundry execution for PostgreSQL pgvector plus
Azure OpenAI chat and embeddings.
"""

from __future__ import annotations

import difflib
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

CREATE TABLE IF NOT EXISTS validation_history (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_name        TEXT        NOT NULL,
    period              TEXT,
    narrative           TEXT        NOT NULL,
    rewritten_narrative TEXT,
    compliance_score    INTEGER,
    overall_verdict     TEXT,
    issues              JSONB,
    full_result         JSONB,
    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id             UUID
);

CREATE INDEX IF NOT EXISTS idx_validation_history_project
    ON validation_history(project_name, validated_at DESC);

CREATE INDEX IF NOT EXISTS idx_validation_history_date
    ON validation_history(validated_at DESC);

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


_VALIDATE_SYSTEM_TEMPLATE = """You are an NDA Narrative Validator. You check project narratives against exactly 9 criteria.

━━━ IMPORTANT RULES ━━━
1. Check ONLY the 9 criteria below. Do not check anything else (sentence order, paragraph structure, etc.).
2. Standard acronyms — DCA, EAC, RAG, P50, P80 — do NOT need expanding. Never flag them.
3. If a criterion does not apply to this project (e.g. no cost movement, no schedule movement), mark it N/A and EXCLUDE it from scoring entirely. Do not penalise for it.
4. Compliance score = round((criteria passed / criteria applicable) × 10). Only applicable criteria count.
5. Be fair — if the content is present in any reasonable form, mark it passed.

━━━ THE 9 CRITERIA ━━━

1. PROJECT DESCRIPTION
   PASS: Narrative contains any statement describing what the project is or does.
   FAIL: Narrative has NO description of the project purpose whatsoever.

2. DCA RAG
   PASS: Narrative mentions the DCA, SRO, or SPA status with any colour (Green/Amber/Red) or directional word. Any phrasing is acceptable — e.g. "DCA remains Green", "DCA is Red", "DCA has deteriorated to Amber", "Delivery Confidence Assessment remains Amber". The content matters, not the exact wording.
   FAIL: There is NO mention of DCA, SRO, or SPA status anywhere in the narrative.
   N/A: DCA is not applicable to this project.

3. PROJECT BENEFIT
   PASS: Narrative contains ANY statement about the status of project benefits or benefit milestones.
   FAIL: There is absolutely NO mention of project benefits or benefit milestones.

4. COMPLETION COST (P50/P80)
   N/A: No P50/P80 cost movement in the reporting period — omit this criterion entirely.
   PASS: Narrative mentions the P50 cost movement with a figure.
   FAIL: There IS cost movement in the data but the narrative does not mention it.

5. SCHEDULE POSITION
   N/A: No schedule movement in the reporting period — omit this criterion entirely.
   PASS: Narrative mentions the schedule position or movement.
   FAIL: There IS schedule movement in the data but the narrative does not mention it.

6. BASELINE RAG
   PASS: Narrative mentions the Baseline RAG status with any colour or directional statement. Any phrasing is acceptable — e.g. "Baseline RAG remains Green", "Baseline RAG is Red", "Baseline RAG remains Amber", "Baseline RAG sits at Red". The content matters, not the exact stem.
   FAIL: There is NO mention of Baseline RAG status at all when it should be reported.
   N/A: Baseline RAG is not applicable to this project.

7. BASELINE MOVEMENT
   N/A: The P50 baseline has not moved — omit this criterion entirely.
   PASS: Narrative mentions the baseline movement.
   FAIL: The baseline HAS moved in the data but the narrative does not mention it.

8. HIGHLIGHTS / ISSUES
   PASS: Narrative describes any notable activities, highlights, issues, risks, or events in the reporting period — whether using a "Highlights" heading or as inline narrative. Descriptions like "In period X was completed…", "The project continues to be impacted by…", "Highlights in period…", "Only issue has been…" all count as PASS.
   FAIL: There are clearly issues or notable activities in the period data but the narrative makes NO mention of any of them — it is purely a status summary with nothing describing what happened in the period.
   N/A: There are genuinely no highlights or issues in the period.

9. CAPABILITY & CAPACITY (CAP/CAP) RAG
   PASS: Narrative mentions Capability & Capacity (or Cap/Cap) RAG status.
   FAIL: Cap/Cap RAG status is not mentioned when it should be.
   N/A: Cap/Cap RAG is not applicable to this project.

━━━ GOOD PRACTICE GUIDANCE (reference only — do not add extra checks from this) ━━━
{guidance}

━━━ LAYER 2 — DATA VALIDATION ━━━
- EAC movement >= GBP 0.1m (flag=material/major) must be explained in narrative
- Schedule slip (positive days) must be mentioned
- RAG change must be acknowledged with reason

━━━ RESPONSE FORMAT ━━━
Return JSON only — no markdown, no extra text:
{{
  "layer1": {{"compliance_score": <int 0-10, round((passed/applicable)×10)>, "issues": [<one item per failing criterion>], "passed": [<one item per passing criterion>]}},
  "layer2": {{"eac_explained": <true|false|"not_applicable">, "schedule_explained": <true|false|"not_applicable">, "data_flag": <"none"|"minor"|"material"|"major">, "issues": []}},
  "rewritten_narrative": "<Complete rewritten narrative fixing all issues as a single flowing paragraph.>",
  "overall_verdict": <"PASS"|"PASS_WITH_WARNINGS"|"FAIL">
}}"""


def _compute_diff_html(original: str, rewritten: str) -> str:
    """
    Word-level diff between original and rewritten narrative.
    Removed words: red strikethrough. Added words: green.
    Returns an HTML string safe for Power Apps HTML label rendering.
    """
    original_words = original.split()
    rewritten_words = rewritten.split()
    matcher = difflib.SequenceMatcher(None, original_words, rewritten_words)
    parts: List[str] = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            parts.append(" ".join(original_words[i1:i2]))
        elif opcode == "replace":
            removed = " ".join(original_words[i1:i2])
            added = " ".join(rewritten_words[j1:j2])
            parts.append(
                f'<span style="color:#dc2626;text-decoration:line-through">{removed}</span>'
                f' <span style="color:#059669">{added}</span>'
            )
        elif opcode == "delete":
            removed = " ".join(original_words[i1:i2])
            parts.append(
                f'<span style="color:#dc2626;text-decoration:line-through">{removed}</span>'
            )
        elif opcode == "insert":
            added = " ".join(rewritten_words[j1:j2])
            parts.append(f'<span style="color:#059669">{added}</span>')
    return " ".join(parts)


def _build_main_result_html(result: Dict) -> str:
    """Build pre-rendered HTML for the Power Apps HtmlViewer from the structured JSON result."""
    layer1 = result.get("layer1", {})
    layer2 = result.get("layer2", {})
    score = layer1.get("compliance_score", 0)
    issues = layer1.get("issues", [])
    passed = layer1.get("passed", [])
    l2_eac = layer2.get("eac_explained", "not_applicable")
    l2_sched = layer2.get("schedule_explained", "not_applicable")
    l2_flag = layer2.get("data_flag", "none")
    l2_issues = layer2.get("issues", [])

    def _icon(val) -> str:
        if val is True:
            return "&#10003;"
        if val is False:
            return "&#10007;"
        return "N/A"

    issues_li = "".join(f"<li>{i}</li>" for i in issues) if issues else "<li>None</li>"
    passed_li = "".join(f"<li style='color:#059669'>&#10003; {p}</li>" for p in passed) if passed else ""
    l2_issues_html = (
        "<br><b>Data Issues:</b><ul>"
        + "".join(f"<li>{i}</li>" for i in l2_issues)
        + "</ul>"
        if l2_issues else ""
    )

    return (
        "<b>Layer 1 &#8212; Guidance &amp; Structure</b><br>"
        f"Compliance Score: {score}/10<br><br>"
        f"<b>Issues Found:</b><ul>{issues_li}</ul>"
        + (f"<b>Rules Met:</b><ul>{passed_li}</ul>" if passed_li else "")
        + "<hr style='border:none;border-top:1px solid #e5e7eb;margin:8px 0'>"
        "<b>Layer 2 &#8212; Data Validation</b><br>"
        f"EAC Explained: {_icon(l2_eac)}&nbsp;&nbsp;"
        f"Schedule Explained: {_icon(l2_sched)}&nbsp;&nbsp;"
        f"Data Flag: <b>{l2_flag}</b>"
        + l2_issues_html
    )


def validate_narrative_pgvector(
    project_name: str,
    narrative_text: str,
    period: str = "",
    conversation_id: Optional[str] = None,
    user_scope: Optional[str] = None,
) -> Dict:
    from agent_runner import validate_narrative as _agent_validate

    query_vector = embed(narrative_text)
    chunks = _retrieve(query_vector, project_name, top_k=5)
    eac_data = _load_eac_data(project_name, period)
    chunks_text = "\n\n".join(
        f"[Retrieved context - {c['project_name']} / {c['period_short_name']} "
        f"(similarity: {c['score']:.2f})]:\n{c['raw_content']}"
        for c in chunks
    )
    guidance_text, guidance_source = get_guidance_text()

    # Inject PGVector context into the narrative passed to the Foundry Agent.
    # The agent receives the full context but uses the JSON system prompt below
    # so it returns structured output identical to the previous direct GPT call.
    enriched_narrative = f"""RETRIEVED DATA CONTEXT:
{chunks_text}

EAC / SCHEDULE MOVEMENT DATA:
{eac_data['summary_text']}
EAC variance flag: {eac_data['flag']}

NARRATIVE TO VALIDATE:
{narrative_text}"""

    agent_response = _agent_validate(
        project_name=project_name,
        narrative_text=enriched_narrative,
        period=period,
        conversation_id=conversation_id,
        user_scope=user_scope,
        instructions_override=_VALIDATE_SYSTEM_TEMPLATE.format(guidance=guidance_text),
    )

    raw = agent_response.get("validation_result", "{}")
    # Strip markdown code fences the agent may wrap around the JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        result = json.loads(raw)
        score = result.get("layer1", {}).get("compliance_score", 0)
        if score >= 8:
            result["overall_verdict"] = "PASS"
        elif score >= 6:
            result["overall_verdict"] = "PASS_WITH_WARNINGS"
        else:
            result["overall_verdict"] = "FAIL"
        rewritten = result.get("rewritten_narrative", "")
        result["diff_html"] = _compute_diff_html(narrative_text, rewritten) if rewritten else ""
        result["main_result_html"] = _build_main_result_html(result)
    except json.JSONDecodeError:
        result = {
            "raw_response": raw,
            "parse_error": True,
            "diff_html": "",
            "main_result_html": "<p style='color:#dc2626'>Failed to parse validation result from AI.</p>",
        }

    result["_meta"] = {
        "project_name": project_name,
        "period": period,
        "chunks_used": len(chunks),
        "eac_flag": eac_data["flag"],
        "eac_variance_m": round((eac_data["eac_variance"] or 0) / 1_000_000, 3),
        "schedule_days": eac_data["schedule_days"],
        "guidance_source": guidance_source,
    }

    if not result.get("parse_error"):
        try:
            save_validation_history(project_name, period, narrative_text, result)
        except Exception:
            logger.warning("Failed to save validation history for %s", project_name, exc_info=True)

    return {
        "conversation_id":     agent_response["conversation_id"],
        "is_new_conversation": agent_response["is_new_conversation"],
        "validation_result":   json.dumps(result, ensure_ascii=False, indent=2),
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


def pa_batch_validate_pgvector(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Power Automate variant of pgvector batch validation.

    Wraps batch_validate_pgvector() — which auto-saves each result to
    validation_history via validate_narrative_pgvector — and returns the
    same flat csv_rows format Power Automate's 'Create CSV table' expects.
    """
    import csv as _csv
    import io as _io

    def _ascii_safe(text: str) -> str:
        return (
            str(text)
            .replace("…", "...").replace("—", "-").replace("–", "-")
            .replace("‘", "'").replace("’", "'")
            .replace("“", '"').replace("”", '"')
            .replace("£", "GBP ")
        )

    def _join_issues(issues: List[str], max_issues: int = 5) -> str:
        if not issues:
            return "None"
        trimmed = issues[:max_issues]
        suffix = f"; ...and {len(issues) - max_issues} more" if len(issues) > max_issues else ""
        return "; ".join(trimmed) + suffix

    base    = batch_validate_pgvector(file_bytes, filename=filename)
    period  = base.get("period", "")
    results = base.get("results", [])

    passed = failed = warned = skipped = errors = 0
    csv_rows: List[Dict] = []

    for r in results:
        status = r.get("status", "error")

        if status == "skipped":
            verdict = "SKIPPED"; skipped += 1
            score = None
            layer1_issues = "No narrative text in file"
            layer2_issues = "N/A"
            rewritten = ""

        elif status == "error":
            verdict = "ERROR"; errors += 1
            score = None
            layer1_issues = _ascii_safe(r.get("validation_result", "Validation error"))
            layer2_issues = "N/A"
            rewritten = ""

        else:
            raw = r.get("validation_result", "{}")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                parsed = {}

            if not parsed or parsed.get("parse_error"):
                verdict = "PARSE_ERROR"; errors += 1
                score = None
                layer1_issues = "Agent response could not be parsed"
                layer2_issues = "N/A"
                rewritten = ""
            else:
                l1    = parsed.get("layer1", {})
                l2    = parsed.get("layer2", {})
                score = l1.get("compliance_score")
                vraw  = parsed.get("overall_verdict", "")
                verdict = vraw if vraw in ("PASS", "PASS_WITH_WARNINGS", "WARN", "FAIL") \
                          else ("PASS" if (score or 0) >= 8 else ("WARN" if (score or 0) >= 6 else "FAIL"))
                layer1_issues = _ascii_safe(_join_issues(l1.get("issues", [])))
                layer2_issues = _ascii_safe(_join_issues(l2.get("issues", [])))
                rewritten     = _ascii_safe(parsed.get("rewritten_narrative", ""))

                if verdict == "PASS":         passed += 1
                elif verdict == "FAIL":       failed += 1
                else:                         warned += 1

        csv_rows.append({
            "Project Name":           _ascii_safe(r.get("project_name", "")),
            "Period":                 period,
            "Verdict":                verdict,
            "Compliance Score":       score if score is not None else "-",
            "Layer 1 Issues":         layer1_issues,
            "Layer 2 Issues":         layer2_issues,
            "AI Rewritten Narrative": rewritten,
        })

    _FIELDS = ["Project Name", "Period", "Verdict", "Compliance Score",
               "Layer 1 Issues", "Layer 2 Issues", "AI Rewritten Narrative"]
    buf = _io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(csv_rows)

    return {
        "period":         period,
        "total":          len(results),
        "passed":         passed,
        "failed":         failed,
        "warned":         warned,
        "skipped":        skipped,
        "errors":         errors,
        "overall_status": "PASS" if (failed == 0 and errors == 0) else "FAIL",
        "csv_rows":       csv_rows,
        "csv_content":    buf.getvalue(),
    }


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


def save_validation_history(
    project_name: str,
    period: str,
    narrative: str,
    result: Dict,
) -> None:
    """Persist one validation result to validation_history. Non-fatal if it fails."""
    layer1 = result.get("layer1", {})
    # Omit rendered HTML blobs — they're large and re-derivable
    stored = {k: v for k, v in result.items() if k not in ("diff_html", "main_result_html")}
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO validation_history (
                    project_name, period, narrative, rewritten_narrative,
                    compliance_score, overall_verdict, issues, full_result
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_name,
                    period or None,
                    narrative,
                    result.get("rewritten_narrative") or None,
                    layer1.get("compliance_score"),
                    result.get("overall_verdict"),
                    json.dumps(layer1.get("issues", [])),
                    json.dumps(stored),
                ),
            )


def get_validation_history(
    project_name: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict:
    """Return paginated validation history rows, newest first."""
    where = "WHERE project_name ILIKE %s" if project_name else ""
    base_params: List[Any] = [f"%{project_name}%"] if project_name else []

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM validation_history {where}", base_params)
            total = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT id, project_name, period, narrative, rewritten_narrative,
                       compliance_score, overall_verdict, issues, validated_at
                FROM validation_history
                {where}
                ORDER BY validated_at DESC
                LIMIT %s OFFSET %s
                """,
                base_params + [limit, offset],
            )
            rows = cur.fetchall()

    return {
        "total": total,
        "items": [
            {
                "id":                  str(r[0]),
                "project_name":        r[1],
                "period":              r[2] or "",
                "narrative":           r[3],
                "rewritten_narrative": r[4] or "",
                "compliance_score":    r[5],
                "overall_verdict":     r[6] or "",
                "issues":              r[7] if isinstance(r[7], list) else [],
                "validated_at":        r[8].isoformat() if r[8] else "",
            }
            for r in rows
        ],
    }


_INTENT_PROMPT = """You are a router. Analyze the user's question about the NDA nuclear decommissioning portfolio and determine the intent.
Output ONLY a JSON object with two keys:
1. "intent": string. Choose from:
   - "portfolio_summary" (asking about overall health, count of red/amber projects, general portfolio status)
   - "project_query" (asking about a specific project or list of projects)
   - "eac_query" (asking specifically about financial movements, EAC, or schedule delays)
   - "general" (greetings, unrelated questions)
2. "project_names": list of strings. If specific projects are mentioned, list them. Otherwise empty list."""

_CHAT_SYSTEM = """You are the Sentra RAG Assistant, an expert AI analyzing the NDA portfolio.
Answer based strictly on the provided Context Data. If the context does not contain the answer, say so.
Return raw compact HTML only. Do not use Markdown or code fences. Do not use <br> tags."""


def _detect_intent(question: str) -> Dict[str, Any]:
    try:
        resp = _gpt_client().chat.completions.create(
            model=_chat_deployment(),
            messages=[
                {"role": "system", "content": _INTENT_PROMPT},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content or "{}")
        return {
            "intent": result.get("intent", "general"),
            "project_names": result.get("project_names", []),
        }
    except Exception as exc:
        logger.error("Intent detection failed: %s", exc)
        return {"intent": "project_query", "project_names": []}


_RAG_COLOR_RE = re.compile(r'\b(red|amber|green)\b', re.IGNORECASE)


def _get_portfolio_summary(period: Optional[str] = None) -> str:
    if period:
        sql = """
            SELECT project_name, dca_rag_status, capability_capacity_rag,
                   eac_variance, schedule_variance_days, period_short_name
            FROM nda_projects
            WHERE period_short_name ILIKE %s
            ORDER BY project_name
        """
        params: tuple = (f"%{period}%",)
        label = f"PORTFOLIO DATA (Period: {period.upper()}):\n"
    else:
        sql = """
            SELECT project_name, dca_rag_status, capability_capacity_rag,
                   eac_variance, schedule_variance_days, period_short_name
            FROM nda_projects
            WHERE period_short_name = (
                SELECT period_short_name FROM nda_projects
                ORDER BY period_short_name DESC LIMIT 1
            )
            ORDER BY project_name
        """
        params = ()
        label = "LATEST PORTFOLIO DATA:\n"

    context = label
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return f"No portfolio data available{' for period ' + period if period else ''}."
            for r in rows:
                eac_v   = r[3] if r[3] is not None else 0.0
                sched_v = r[4] if r[4] is not None else 0
                context += (
                    f"- {r[0]} ({r[5]}): RAG={r[1]}, CapRAG={r[2]}, "
                    f"EAC Variance=£{eac_v/1_000_000:.2f}m, Schedule Slip={sched_v} days\n"
                )
    return context


def _get_status_filtered_context(rag_status: str, period: Optional[str] = None) -> str:
    """Direct query for projects matching a RAG colour, optionally filtered by period."""
    if period:
        sql = """
            SELECT project_name, period_short_name, dca_rag_status,
                   capability_capacity_rag, eac_variance, schedule_variance_days, narrative_text
            FROM nda_projects
            WHERE dca_rag_status ILIKE %s
              AND period_short_name ILIKE %s
            ORDER BY project_name
        """
        params: tuple = (f"%{rag_status}%", f"%{period}%")
        label = f"PROJECTS WITH {rag_status.upper()} RAG STATUS IN PERIOD {period.upper()}:\n"
    else:
        sql = """
            SELECT project_name, period_short_name, dca_rag_status,
                   capability_capacity_rag, eac_variance, schedule_variance_days, narrative_text
            FROM nda_projects
            WHERE dca_rag_status ILIKE %s
              AND period_short_name = (
                  SELECT period_short_name FROM nda_projects
                  ORDER BY period_short_name DESC LIMIT 1
              )
            ORDER BY project_name
        """
        params = (f"%{rag_status}%",)
        label = f"PROJECTS WITH {rag_status.upper()} RAG STATUS (LATEST PERIOD):\n"

    context = label
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return f"No projects found with {rag_status} RAG status{' in period ' + period if period else ' in the latest period'}."
            for r in rows:
                eac_v   = r[4] if r[4] is not None else 0.0
                sched_v = r[5] if r[5] is not None else 0
                narrative_snippet = (r[6] or "")[:300]
                context += (
                    f"- {r[0]} ({r[1]}): DCA RAG={r[2]}, CapRAG={r[3]}, "
                    f"EAC Variance=£{eac_v/1_000_000:.2f}m, Schedule Slip={sched_v} days\n"
                    f"  Narrative: {narrative_snippet}\n"
                )
    return context


def _get_eac_context(project_names: List[str]) -> str:
    if not project_names:
        sql = """
            SELECT project_name, period_short_name, eac_variance,
                   schedule_variance_days, summary_text
            FROM nda_eac_variance
            ORDER BY abs(eac_variance) DESC LIMIT 10
        """
        params: tuple = ()
    else:
        sql = """
            SELECT project_name, period_short_name, eac_variance,
                   schedule_variance_days, summary_text
            FROM nda_eac_variance
            WHERE lower(project_name) LIKE lower(%s)
            ORDER BY period_short_name DESC LIMIT 5
        """
        params = (f"%{project_names[0]}%",)

    context = "EAC & SCHEDULE VARIANCES:\n"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return "No EAC variance data found."
            for r in rows:
                context += (
                    f"- {r[0]} ({r[1]}): Variance £{r[2]/1_000_000:.2f}m, "
                    f"Slip {r[3]} days. {r[4]}\n"
                )
    return context


def _vector_chat_context(
    question: str,
    project_names: List[str],
    top_k: int = 5,
    periods: Optional[List[str]] = None,
) -> str:
    enhanced_query = question
    if project_names:
        enhanced_query += " " + " ".join(project_names)
    query_vector = embed(enhanced_query)
    context = "RETRIEVED NARRATIVE CONTEXT:\n"

    with DBConnection() as conn:
        with conn.cursor() as cur:
            if periods and project_names:
                for period in periods:
                    for pname in project_names:
                        cur.execute(
                            """
                            SELECT project_name, period_short_name, raw_content,
                                   1 - (embedding <=> %s::vector) AS score
                            FROM nda_projects
                            WHERE lower(period_short_name) = lower(%s)
                              AND lower(project_name) LIKE lower(%s)
                            ORDER BY embedding <=> %s::vector
                            LIMIT 1;
                            """,
                            (query_vector, period, f"%{pname}%", query_vector),
                        )
                        row = cur.fetchone()
                        if row:
                            context += f"--- {row[0]} ({row[1]}) [Score: {row[3]:.2f}] ---\n{row[2]}\n\n"
                if context != "RETRIEVED NARRATIVE CONTEXT:\n":
                    return context

            cur.execute(
                """
                SELECT project_name, period_short_name, raw_content,
                       1 - (embedding <=> %s::vector) AS score
                FROM nda_projects
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (query_vector, query_vector, top_k),
            )
            rows = cur.fetchall()
            if not rows:
                return "No matching narrative data found."
            for r in rows:
                context += f"--- {r[0]} ({r[1]}) [Score: {r[3]:.2f}] ---\n{r[2]}\n\n"

    return context


def chat_pgvector(question: str, session_id: Optional[str] = None) -> Dict:
    # Load history from blob storage — where _agent_chat actually persists turns.
    # Previously this loaded from PostgreSQL (via _load_history) but _save_turn was
    # never called in this path, so PostgreSQL history was always empty and
    # project name recovery for follow-up questions never worked.
    if session_id:
        from agent_runner import _load_conversation_history
        history = _load_conversation_history(session_id) or []
    else:
        session_id = str(uuid.uuid4())
        history = []

    # Detect intent and extract project/period references
    intent_data = _detect_intent(question)
    intent = intent_data["intent"]
    projects = intent_data["project_names"]
    periods = [m.upper() for m in re.findall(r"\bP\d{2}\b", question, re.IGNORECASE)]

    # Recover project names from the last assistant turn for follow-up questions
    # e.g. "tell me about BEPPS2" → "what is its EAC?" resolves BEPPS2 from history
    if not projects and history:
        last_assistant = next(
            (m["content"] for m in reversed(history) if m["role"] == "assistant"), ""
        )
        if last_assistant:
            prior = _detect_intent(last_assistant)
            projects = prior.get("project_names", [])

    logger.info("pgvector chat — intent: %s, projects: %s, periods: %s, session: %s",
                intent, projects, periods, session_id)

    # Detect RAG colour mentions (Red / Amber / Green) in the question
    rag_colors = [m.group(0) for m in _RAG_COLOR_RE.finditer(question)]

    # Gather context — RAG colour filter takes priority over intent routing
    if rag_colors:
        context = _get_status_filtered_context(rag_colors[0], periods[0] if periods else None)
    elif intent == "portfolio_summary":
        context = _get_portfolio_summary(periods[0] if periods else None)
    elif intent == "eac_query":
        context = _get_eac_context(projects) + "\n\n" + _vector_chat_context(question, projects, top_k=2, periods=periods)
    elif intent == "general":
        context = "No specific NDA project context needed for general questions."
    else:
        context = _vector_chat_context(question, projects, top_k=5, periods=periods)

    from chat import run_chat as _agent_chat

    enriched_question = f"[PGVECTOR CONTEXT]\n{context}\n\n[USER QUESTION]\n{question}"
    return _agent_chat(question=enriched_question, session_id=session_id)
