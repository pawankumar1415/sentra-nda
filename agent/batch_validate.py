"""
agent/batch_validate.py — Batch narrative validation using the AI Foundry Agent.

Pipeline for POST /api/batch-validate:
  1. Parse the uploaded MPPR Excel file to extract project names + narratives.
  2. For each project with a narrative, call validate_narrative() (single route).
  3. Aggregate results into a consolidated list and return.

The response shape deliberately mirrors the RAG batch endpoint so the frontend
can handle both backends with minimal branching:
    {
        "status":  "ok",
        "period":  "P07",
        "total":   N,
        "results": [
            {
                "project_name":      "Dounreay Shaft",
                "status":            "ok" | "skipped" | "error",
                "validation_result": "<agent free-form text>",
                "conversation_id":   "<uuid>"
            },
            ...
        ]
    }
"""

from __future__ import annotations

import io
import logging
import re
from typing import Dict, List, Tuple

import pandas as pd

from agent_runner import validate_narrative

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Excel parser (stripped-down version for the agent — mirrors rag_function/ingest.py)
# ─────────────────────────────────────────────────────────────────────────────
_PERIOD_RE  = re.compile(r"\b(P\d{2})\b", re.IGNORECASE)
_RAG_VALUES = {"r", "a", "g", "-", "n/a", "tbd"}


def _safe(value, default: str = "") -> str:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _extract_period(filename: str, df: "pd.DataFrame") -> str:
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
    """
    Parse the '5a)NDA MPPR' sheet and return (period, projects).

    Each project dict contains:
        project_name, narrative_text
    """
    xl          = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_name  = next((s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None)
    if not sheet_name:
        raise ValueError(f"Sheet '5a)NDA MPPR' not found. Available: {xl.sheet_names}")

    df     = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    period = _extract_period(filename, df)

    projects: List[Dict] = []
    for i in range(6, len(df)):
        row  = df.iloc[i]
        col0 = _safe(row.iloc[0]) if len(row) > 0 else ""
        col1 = _safe(row.iloc[1]) if len(row) > 1 else ""
        col3 = _safe(row.iloc[3]) if len(row) > 3 else ""

        is_data_row = (
            col0 == ""
            and col1 != ""
            and len(col1) >= 3
            and col3.lower() in _RAG_VALUES
        )
        if not is_data_row:
            continue

        project_name   = col1
        narrative_text = ""
        for j in range(i + 1, min(i + 4, len(df))):
            nrow = df.iloc[j]
            nc0  = _safe(nrow.iloc[0]) if len(nrow) > 0 else ""
            nc1  = _safe(nrow.iloc[1]) if len(nrow) > 1 else ""
            if nc0 and nc1 and len(nc1) > 40:
                narrative_text = nc1
                break

        projects.append({"project_name": project_name, "narrative_text": narrative_text})

    return period, projects


# ─────────────────────────────────────────────────────────────────────────────
# Batch pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_batch_validate(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Full agent batch validation pipeline.

    Args:
        file_bytes: Raw Excel file bytes.
        filename:   Original filename (used for period extraction).

    Returns:
        Consolidated JSON dict with period, total, and per-project results.
    """
    logger.info("Starting agent batch validation (filename=%s)", filename or "<none>")

    period, projects = parse_excel(file_bytes, filename=filename)
    if not projects:
        return {
            "status":  "warning",
            "period":  period,
            "message": "No project rows found in the Excel file.",
            "total":   0,
            "results": [],
        }

    logger.info("Agent batch: %d projects from period %s", len(projects), period)

    results = []
    for project in projects:
        project_name   = project["project_name"]
        narrative_text = project.get("narrative_text", "").strip()

        if not narrative_text:
            results.append({
                "project_name":      project_name,
                "status":            "skipped",
                "validation_result": "No narrative text in the Excel file for this project.",
                "conversation_id":   None,
            })
            continue

        try:
            val = validate_narrative(
                project_name=project_name,
                narrative_text=narrative_text,
                period=period,
            )
            results.append({
                "project_name":      project_name,
                "status":            "ok",
                "validation_result": val["validation_result"],
                "conversation_id":   val["conversation_id"],
            })
        except Exception as exc:
            logger.exception("Agent validation failed for %s", project_name)
            results.append({
                "project_name":      project_name,
                "status":            "error",
                "validation_result": f"Validation error: {exc}",
                "conversation_id":   None,
            })

    return {
        "status":  "ok",
        "period":  period,
        "total":   len(results),
        "results": results,
    }
