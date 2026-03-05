"""
rag_function/validate.py — RAG retrieval + EAC check + GPT structured response.

Pipeline for POST /api/validate:
  1. Embed the user's query / narrative text
  2. Cosine similarity search on nda_projects (top-k)
  3. Load EAC variance data for the specific project
  4. Build an augmented prompt with retrieved context + EAC data
  5. Call Azure OpenAI GPT for a structured two-layer validation response

Required environment variables (in addition to db.py / embedder.py vars):
    AZURE_OPENAI_CHAT_DEPLOYMENT    e.g. gpt-5.1-chat
    EAC_VARIANCE_FILE               path to lifecycle_eac_variance.xlsx
                                    (default: ../NDA Data/lifecycle_eac_variance.xlsx)
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any, Dict, List, Optional

from openai import AzureOpenAI

from db import DBConnection
from embedder import embed

logger = logging.getLogger(__name__)

# ── GPT client (reused across warm invocations) ───────────────────────────────
_gpt_client: AzureOpenAI | None = None


def _get_gpt_client() -> AzureOpenAI:
    global _gpt_client
    if _gpt_client is None:
        _gpt_client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
    return _gpt_client


def _chat_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.1-chat")


# ── EAC variance lookup ───────────────────────────────────────────────────────
_EAC_THRESHOLD_LOW   = 50_000    # £50k  → no comment needed
_EAC_THRESHOLD_HIGH  = 100_000   # £0.1m → must appear in narrative
_EAC_THRESHOLD_MAJOR = 500_000   # £0.5m → needs explicit explanation


def _load_eac_data(project_name: str, period: Optional[str]) -> Dict[str, Any]:
    """
    Retrieve EAC movement data for the given project from PostgreSQL.

    Returns a dict with keys: eac_variance, schedule_days, flag, summary_text
    Falls back gracefully if not found.
    """
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
    params = [f"%{project_name}%"]
    
    if period:
        sql += " AND lower(period_short_name) = lower(%s)"
        params.append(period)
        
    sql += " LIMIT 1;"

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()

            # Fallback: If no match with period, try just the project name
            # since EAC variance periods might be formatted differently (e.g. '2025-P11')
            if not row and period:
                logger.info("EAC not found for '%s' in period '%s'. Trying without period...", project_name, period)
                fallback_sql = """
                    SELECT eac_variance, schedule_variance_days, flag, summary_text
                    FROM nda_eac_variance
                    WHERE lower(project_name) LIKE lower(%s)
                    ORDER BY period_short_name DESC LIMIT 1;
                """
                cur.execute(fallback_sql, (f"%{project_name}%",))
                row = cur.fetchone()

    if not row:
        logger.warning("EAC variance data not found in DB for %s", project_name)
        return default

    return {
        "eac_variance": row[0],
        "schedule_days": row[1],
        "flag": row[2],
        "summary_text": row[3],
    }


# ── Vector retrieval ──────────────────────────────────────────────────────────
def _retrieve(query_vector: List[float], project_name: Optional[str], top_k: int = 5) -> List[Dict]:
    """
    Run pgvector cosine similarity search. Optionally filter by project name.
    Returns list of dicts with keys: project_name, period_short_name, raw_content, score.
    """
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
            "project_name":      r[0],
            "period_short_name": r[1],
            "raw_content":       r[2],
            "narrative_text":    r[3],
            "score":             float(r[4]),
        }
        for r in rows
    ]


# ── Prompt builder ────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are the NDA Narrative Validation Agent. You validate project narrative text
for NDA portfolio reporting using two layers:

LAYER 1 — GUIDANCE & STRUCTURE: Check the narrative against Good Practice rules:
- Must read as flowing prose (not bullet points)
- Must include: DCA/RAG status sentence, benefit milestone sentence, EAC sentence, schedule sentence, 
  contingency/risk sentence, baseline RAG sentence, highlights sentence, Capability & Capacity RAG sentence
- Acronyms expanded on first use; dates written in full; no building numbers; no document references
- Cost figures must match reported data

LAYER 2 — DATA VALIDATION: Check if material movements are explained:
- EAC movement ≥ £0.1m (flag=material/major) → must be explained in narrative
- Schedule slip (positive days) → must be mentioned
- RAG change → must be acknowledged with reason

Always respond in this exact JSON format:
{
  "layer1": {
    "compliance_score": <int 0-10>,
    "issues": [<list of specific issues found>],
    "passed": [<list of rules that were met>]
  },
  "layer2": {
    "eac_explained": <true|false|"not_applicable">,
    "schedule_explained": <true|false|"not_applicable">,
    "data_flag": <"none"|"minor"|"material"|"major">,
    "issues": [<list of data movement issues not addressed>]
  },
  "suggestions": [<up to 3 specific rewritten sentences using Good Practice templates>],
  "overall_verdict": <"PASS"|"PASS_WITH_WARNINGS"|"FAIL">
}"""


def _build_user_message(
    narrative: str,
    project_name: str,
    retrieved_chunks: List[Dict],
    eac_data: Dict,
) -> str:
    chunks_text = "\n\n".join(
        f"[Retrieved context — {c['project_name']} / {c['period_short_name']} "
        f"(similarity: {c['score']:.2f})]:\n{c['raw_content']}"
        for c in retrieved_chunks
    )
    return f"""PROJECT: {project_name}

RETRIEVED DATA CONTEXT (from indexed project records):
{chunks_text}

EAC / SCHEDULE MOVEMENT DATA:
{eac_data['summary_text']}
EAC variance flag: {eac_data['flag']}

NARRATIVE TO VALIDATE:
{narrative}

Validate this narrative. Return JSON only."""


# ── Public entry point ────────────────────────────────────────────────────────
def run_validate(
    narrative: str,
    project_name: str,
    period: Optional[str] = None,
    top_k: int = 5,
) -> Dict:
    """
    Full validation pipeline. Returns the structured JSON validation result.

    Args:
        narrative:    The narrative text to validate.
        project_name: Used for EAC lookup and vector search filter.
        period:       Optional period filter (e.g. "P07") for EAC lookup.
        top_k:        Number of similar project chunks to retrieve.
    """
    logger.info("Validating narrative for project: %s", project_name)

    # 1 — embed the narrative text itself as the query
    query_vector = embed(narrative)

    # 2 — retrieve similar project data from PGVector
    chunks = _retrieve(query_vector, project_name, top_k=top_k)
    logger.info("Retrieved %d chunks from PGVector", len(chunks))

    # 3 — load EAC variance data
    eac_data = _load_eac_data(project_name, period)
    logger.info("EAC flag: %s | variance: £%.2fm", eac_data["flag"],
                eac_data["eac_variance"] / 1_000_000)

    # 4 — call GPT
    user_message = _build_user_message(narrative, project_name, chunks, eac_data)

    gpt  = _get_gpt_client()
    resp = gpt.chat.completions.create(
        model=_chat_deployment(),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        temperature=1,      # gpt-5.1-chat only supports default temperature (1)
        response_format={"type": "json_object"},
    )

    raw_json = resp.choices[0].message.content
    try:
        result = json.loads(raw_json)
    except json.JSONDecodeError:
        result = {"raw_response": raw_json, "parse_error": True}

    # Attach metadata
    result["_meta"] = {
        "project_name":   project_name,
        "period":         period,
        "chunks_used":    len(chunks),
        "eac_flag":       eac_data["flag"],
        "eac_variance_m": round(eac_data["eac_variance"] / 1_000_000, 3),
        "schedule_days":  eac_data["schedule_days"],
    }
    return result
