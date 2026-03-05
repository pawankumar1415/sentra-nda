"""
rag_function/ingest.py — Excel → clean → embed → upsert to PGVector.

The NDA MPPR sheet ('5a)NDA MPPR') is a FORMATTED REPORT, not a tabular
spreadsheet. Each project spans 2–3 rows:
  - Data row:      col 0 = empty, col 1 = project name,
                   col 3 = DCA RAG status (R/A/G), col 7+ = numeric data
  - Narrative row: col 0 = project name, col 1 = narrative paragraph text
  - Blank row:     separator

Called by the /api/ingest HTTP route in function_app.py.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Tuple

import pandas as pd

from db import DBConnection
from embedder import embed_batch

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _safe(value: Any, default: str = "") -> str:
    """Convert a cell value to a clean string."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Period extraction helpers
# ─────────────────────────────────────────────────────────────────────────────
_PERIOD_RE = re.compile(r"\b(P\d{2})\b", re.IGNORECASE)


def _extract_period_from_filename(filename: str) -> str:
    """Extract period like 'P07' from filename. Returns '' if not found."""
    if not filename:
        return ""
    m = _PERIOD_RE.search(filename)
    return m.group(1).upper() if m else ""


def _extract_period_from_sheet(df: "pd.DataFrame") -> str:
    """Scan the first few rows of the sheet for a P## period reference."""
    for i in range(min(6, len(df))):
        for j in range(min(10, len(df.columns))):
            cell = _safe(df.iloc[i, j])
            m = _PERIOD_RE.search(cell)
            if m:
                return m.group(1).upper()
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Excel parser — NDA MPPR multi-row format
# ─────────────────────────────────────────────────────────────────────────────
# DCA RAG letters that identify a project data row
_RAG_VALUES = {"r", "a", "g", "-", "n/a", "tbd"}


def parse_excel(file_bytes: bytes, filename: str = "") -> Tuple[str, List[Dict]]:
    """
    Parse the '5a)NDA MPPR' sheet from an NDA Executive Project Summary Excel.

    The sheet has NO traditional column headers. Each project spans 2-3 rows:
      Row N:   col0=empty, col1=project name, col3=DCA (R/A/G), col7+=numeric data
      Row N+1: col0=project name, col1=narrative text
      Row N+2: blank separator

    Args:
        file_bytes: Raw Excel file bytes.
        filename:   Original filename (used to extract period, e.g. 'P07 Exec...').

    Returns:
        (period_name, list_of_project_dicts)
    """
    xl = pd.ExcelFile(io.BytesIO(file_bytes))

    sheet_name = next(
        (s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None
    )
    if not sheet_name:
        raise ValueError(
            f"Sheet '5a)NDA MPPR' not found. Available: {xl.sheet_names}"
        )

    # Read raw — no header, no skipping
    df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    logger.info("Raw sheet '%s': %d rows × %d cols", sheet_name, len(df), len(df.columns))

    # Extract period: filename first, then sheet content, then fallback
    period = _extract_period_from_filename(filename)
    if not period:
        period = _extract_period_from_sheet(df)
    if not period:
        period = "UNKNOWN"
        logger.warning("Could not determine period from filename or sheet content")
    logger.info("Detected period: %s", period)

    projects: List[Dict] = []

    for i in range(6, len(df)):  # data rows start at index 6
        row = df.iloc[i]

        # Value in each key column
        col0 = _safe(row.iloc[0]) if len(row) > 0 else ""
        col1 = _safe(row.iloc[1]) if len(row) > 1 else ""
        col3 = _safe(row.iloc[3]) if len(row) > 3 else ""

        # A project DATA row has:
        #   col0 = empty, col1 = project name (non-empty), col3 = RAG letter
        is_data_row = (
            col0 == ""
            and col1 != ""
            and len(col1) >= 3           # not a number or single char
            and col3.lower() in _RAG_VALUES
        )

        if not is_data_row:
            continue

        project_name = col1
        dca_rag      = col3.upper()

        # ── Extract numeric EAC/schedule columns ──────────────────────────────
        # Scan all columns for numeric data (cast those that are numeric)
        numeric_cols: Dict[int, float] = {}
        for ci in range(4, len(row)):
            val = row.iloc[ci]
            if isinstance(val, (int, float)) and not pd.isna(val):
                numeric_cols[ci] = float(val)

        # Heuristic positions (may vary slightly between period files):
        # col 7  → Business Case (P50) cost
        # col 12 → Current P50 EAC (£m)
        # col 13 → EAC vs Last Period (£m) — the variance
        # col 15 → Schedule end-date variance (days)
        eac_total    = _safe_float(numeric_cols.get(12, numeric_cols.get(13, 0.0)))
        eac_variance = _safe_float(numeric_cols.get(13, numeric_cols.get(14, 0.0)))
        sched_days   = _safe_int(numeric_cols.get(15, numeric_cols.get(16, 0)))

        # ── Find the narrative on the next row ────────────────────────────────
        narrative_text = ""
        for j in range(i + 1, min(i + 4, len(df))):
            nrow  = df.iloc[j]
            nc0   = _safe(nrow.iloc[0]) if len(nrow) > 0 else ""
            nc1   = _safe(nrow.iloc[1]) if len(nrow) > 1 else ""
            # Narrative row: col0 matches project name, col1 is long text
            if nc0 and nc1 and len(nc1) > 40:
                narrative_text = nc1
                break

        raw_text = (
            f"Project: {project_name} | "
            f"DCA RAG: {dca_rag} | "
            f"EAC (£m): {eac_total:.3f} | "
            f"EAC Variance (£m): {eac_variance:.3f} | "
            f"Schedule Variance (days): {sched_days} | "
            f"Narrative: {narrative_text}"
        )

        projects.append({
            "project_id":              f"{period}|{project_name}",
            "project_name":            project_name,
            "period_short_name":       period,
            "rag_status":              dca_rag,
            "dca_rag_status":          dca_rag,
            "capability_capacity_rag": "",
            "eac_total":               eac_total,
            "eac_variance":            eac_variance,
            "schedule_variance_days":  sched_days,
            "narrative_text":          narrative_text,
            "raw_content":             raw_text,
        })

    logger.info("Parsed %d projects from '%s'", len(projects), sheet_name)
    return period, projects


# ─────────────────────────────────────────────────────────────────────────────
# Ingest pipeline
# ─────────────────────────────────────────────────────────────────────────────
def run_ingest(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Full ingest pipeline:
      1. Parse Excel → project rows
      2. Batch-embed raw_content strings
      3. Upsert all rows into nda_projects

    Args:
        file_bytes: Raw Excel file bytes.
        filename:   Original filename for period extraction.

    Returns a summary dict suitable for the HTTP response.
    """
    logger.info("Starting ingest pipeline (filename=%s)", filename or "<none>")

    period, projects = parse_excel(file_bytes, filename=filename)
    if not projects:
        return {"status": "warning", "message": "No project rows found", "indexed": 0}

    logger.info("Parsed %d projects from period %s", len(projects), period)

    # 1 — batch embed all raw_content strings
    texts   = [p["raw_content"] for p in projects]
    vectors = embed_batch(texts)

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
