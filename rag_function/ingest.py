"""
rag_function/ingest.py — Excel → clean → embed → upsert to PGVector.

Parses the '5a)NDA MPPR' sheet (columns A–AK), builds one text chunk
per project row, embeds in batch via Azure OpenAI, and upserts into
the nda_projects table using ON CONFLICT DO UPDATE.

Called by the /api/ingest HTTP route in function_app.py.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Dict, List, Tuple

import pandas as pd

from .db import DBConnection
from .embedder import embed_batch

logger = logging.getLogger(__name__)

# Columns expected in '5a)NDA MPPR' (header row is row 3, 0-indexed row 2)
# These map to the Excel column letters A–AK.
_REQUIRED_COLS = [
    "Project / Programme Code",       # A
    "Project / Programme Title",      # B
    "Period Short Name",              # F  (approx — varies by period file)
    "DCA RAG",                        # H
    "Capability & Capacity RAG",      # I
    "End Date Variance (Days)",       # T  (schedule)
    "P50 EAC (£m)",                   # W
    "P50 EAC Variance",               # X  (vs prev period)
    "Narrative",                      # AJ or AK
]


def _safe(value: Any, default: str = "") -> str:
    """Convert a cell value to a clean string."""
    if pd.isna(value):
        return default
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


def _build_text_chunk(row: pd.Series, col_map: Dict[str, str]) -> str:
    """
    Concatenate all meaningful fields into a single searchable text string.
    This is what gets embedded — the more context the better.
    """
    parts = [
        f"Project: {_safe(row.get(col_map.get('title', ''), ''))}",
        f"Code: {_safe(row.get(col_map.get('code', ''), ''))}",
        f"Period: {_safe(row.get(col_map.get('period', ''), ''))}",
        f"DCA RAG Status: {_safe(row.get(col_map.get('dca_rag', ''), ''))}",
        f"Capability & Capacity RAG: {_safe(row.get(col_map.get('cap_rag', ''), ''))}",
        f"EAC (£m): {_safe(row.get(col_map.get('eac', ''), ''))}",
        f"EAC Variance (£m): {_safe(row.get(col_map.get('eac_var', ''), ''))}",
        f"Schedule Variance (days): {_safe(row.get(col_map.get('sched', ''), ''))}",
        f"Narrative: {_safe(row.get(col_map.get('narrative', ''), ''))}",
    ]
    return " | ".join(p for p in parts if not p.endswith(": "))


def _find_col(df: pd.DataFrame, keywords: List[str]) -> str:
    """Find the first column whose name contains all given keywords (case-insensitive)."""
    for col in df.columns:
        col_l = col.lower()
        if all(k.lower() in col_l for k in keywords):
            return col
    return ""


def _build_col_map(df: pd.DataFrame) -> Dict[str, str]:
    """
    Map logical field names to actual DataFrame column names.
    Uses fuzzy keyword matching to handle slight column name variations
    across different period Excel files.
    """
    return {
        "code":      _find_col(df, ["code"]),
        "title":     _find_col(df, ["title"]) or _find_col(df, ["programme"]),
        "period":    _find_col(df, ["period", "short"]),
        "dca_rag":   _find_col(df, ["dca"]) or _find_col(df, ["rag"]),
        "cap_rag":   _find_col(df, ["capability"]),
        "eac":       _find_col(df, ["p50", "eac", "£"]),
        "eac_var":   _find_col(df, ["eac", "variance"]),
        "sched":     _find_col(df, ["end date", "variance", "day"])
                     or _find_col(df, ["schedule", "variance"]),
        "narrative": _find_col(df, ["narrative"]),
    }


def _detect_header_row(xl: "pd.ExcelFile", sheet_name: str, max_scan: int = 12) -> int:
    """
    Scan first max_scan rows to find the one with recognisable NDA MPPR column keywords.
    Returns 0-indexed row number to use as header=. Defaults to 2 if not found.
    """
    MARKERS = ["period", "project", "programme", "code", "title", "narrative", "rag"]
    df_raw = pd.read_excel(xl, sheet_name=sheet_name, header=None, nrows=max_scan)
    for idx, row in df_raw.iterrows():
        row_text = " ".join(str(v).lower() for v in row if not pd.isna(v))
        hits = sum(1 for m in MARKERS if m in row_text)
        if hits >= 3:
            logger.info("Auto-detected header row at index %d", idx)
            return int(idx)
    logger.warning("Could not auto-detect header row — defaulting to row 2")
    return 2


def parse_excel(file_bytes: bytes) -> Tuple[str, List[Dict]]:
    """
    Parse the '5a)NDA MPPR' sheet from an Excel file.

    Returns:
        (period_name, list_of_project_dicts)
    """
    xl = pd.ExcelFile(io.BytesIO(file_bytes))

    sheet_name = next(
        (s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None
    )
    if not sheet_name:
        raise ValueError(
            f"Sheet '5a)NDA MPPR' not found. Sheets present: {xl.sheet_names}"
        )

    # Auto-detect which row holds the column headers
    header_row = _detect_header_row(xl, sheet_name)
    df = pd.read_excel(xl, sheet_name=sheet_name, header=header_row)

    # Drop fully empty rows/cols; strip newlines from multi-line Excel headers
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    col_map  = _build_col_map(df)
    period   = ""
    projects = []

    for _, row in df.iterrows():
        code = _safe(row.get(col_map.get("code", ""), ""))
        if not code:
            continue   # skip blank / header-repeat rows

        if not period:
            period = _safe(row.get(col_map.get("period", ""), "UNKNOWN"))

        project_id = f"{period}|{code}"
        raw_text   = _build_text_chunk(row, col_map)

        projects.append({
            "project_id":              project_id,
            "project_name":            _safe(row.get(col_map.get("title", ""), "")),
            "period_short_name":       period,
            "rag_status":              _safe(row.get(col_map.get("dca_rag", ""), "")),
            "dca_rag_status":          _safe(row.get(col_map.get("dca_rag", ""), "")),
            "capability_capacity_rag": _safe(row.get(col_map.get("cap_rag", ""), "")),
            "eac_total":               _safe_float(row.get(col_map.get("eac", ""), 0)),
            "eac_variance":            _safe_float(row.get(col_map.get("eac_var", ""), 0)),
            "schedule_variance_days":  _safe_int(row.get(col_map.get("sched", ""), 0)),
            "narrative_text":          _safe(row.get(col_map.get("narrative", ""), "")),
            "raw_content":             raw_text,
        })

    return period, projects


def run_ingest(file_bytes: bytes) -> Dict:
    """
    Full ingest pipeline:
      1. Parse Excel → project rows
      2. Batch-embed raw_content strings
      3. Upsert all rows into nda_projects

    Returns a summary dict suitable for the HTTP response.
    """
    logger.info("Starting ingest pipeline")

    period, projects = parse_excel(file_bytes)
    if not projects:
        return {"status": "warning", "message": "No project rows found", "indexed": 0}

    logger.info("Parsed %d projects from period %s", len(projects), period)

    # 1 — batch embed all raw_content strings
    texts    = [p["raw_content"] for p in projects]
    vectors  = embed_batch(texts)

    # 2 — upsert to PostgreSQL
    upsert_sql = """
        INSERT INTO nda_projects (
            project_id, project_name, period_short_name,
            rag_status, dca_rag_status, capability_capacity_rag,
            eac_total, eac_variance, schedule_variance_days,
            narrative_text, raw_content, embedding, indexed_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (project_id) DO UPDATE SET
            project_name            = EXCLUDED.project_name,
            rag_status              = EXCLUDED.rag_status,
            dca_rag_status          = EXCLUDED.dca_rag_status,
            capability_capacity_rag = EXCLUDED.capability_capacity_rag,
            eac_total               = EXCLUDED.eac_total,
            eac_variance            = EXCLUDED.eac_variance,
            schedule_variance_days  = EXCLUDED.schedule_variance_days,
            narrative_text          = EXCLUDED.narrative_text,
            raw_content             = EXCLUDED.raw_content,
            embedding               = EXCLUDED.embedding,
            indexed_at              = NOW();
    """

    with DBConnection() as conn:
        with conn.cursor() as cur:
            for project, vector in zip(projects, vectors):
                cur.execute(upsert_sql, (
                    project["project_id"],
                    project["project_name"],
                    project["period_short_name"],
                    project["rag_status"],
                    project["dca_rag_status"],
                    project["capability_capacity_rag"],
                    project["eac_total"],
                    project["eac_variance"],
                    project["schedule_variance_days"],
                    project["narrative_text"],
                    project["raw_content"],
                    vector,
                ))

    logger.info("Upserted %d projects to nda_projects", len(projects))

    return {
        "status":  "ok",
        "period":  period,
        "indexed": len(projects),
    }
