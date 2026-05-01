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


# ─────────────────────────────────────────────────────────────────────────────
# Power Automate batch validate — flat csv_rows response
# Called by POST /api/pa-batch-validate (separate route, existing route untouched)
# ─────────────────────────────────────────────────────────────────────────────

def _verdict_from_text(text: str) -> str:
    """Best-effort verdict extraction from free-form agent prose."""
    upper = text.upper()
    # Explicit verdict lines take priority
    for prefix in ("OVERALL: PASS", "VERDICT: PASS", "OVERALL VERDICT: PASS"):
        if prefix in upper:
            return "PASS"
    for prefix in ("OVERALL: FAIL", "VERDICT: FAIL", "OVERALL VERDICT: FAIL"):
        if prefix in upper:
            return "FAIL"
    for prefix in ("OVERALL: WARN", "VERDICT: WARN", "OVERALL VERDICT: WARN"):
        if prefix in upper:
            return "WARN"
    # Fallback keyword scan
    if "FAIL" in upper:
        return "FAIL"
    if "WARNING" in upper or "WARN" in upper:
        return "WARN"
    if "PASS" in upper:
        return "PASS"
    return "UNKNOWN"


def _issues_from_text(text: str) -> str:
    """Extract bullet / numbered issue lines from free-form agent prose."""
    issues = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        is_bullet = s[0] in ("-", "•", "*", "–")
        is_numbered = len(s) > 2 and s[0].isdigit() and s[1] in ".)"
        has_issue_kw = any(kw in s.lower() for kw in (
            "issue", "missing", "incorrect", "should", "must",
            "lacks", "not mentioned", "failed to", "no reference",
        ))
        if is_bullet or is_numbered or has_issue_kw:
            clean = s.lstrip("-•*–0123456789.) ").strip()
            if clean:
                issues.append(clean)
    return "; ".join(issues) if issues else "None"


def run_pa_batch_validate(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Power Automate variant of batch validation.

    Runs the same validation pipeline as run_agent_batch_validate() but returns
    a flat csv_rows array that Power Automate's 'Create CSV table' action can
    consume directly, plus summary counts for the email subject/body.

    Existing /api/batch-validate route and run_agent_batch_validate() are
    completely untouched.
    """
    logger.info("Starting PA batch validation (filename=%s)", filename or "<none>")

    base = run_agent_batch_validate(file_bytes, filename=filename)
    period  = base.get("period", "")
    results = base.get("results", [])

    csv_rows = []
    passed = failed = warned = skipped = errors = 0

    for r in results:
        status = r.get("status", "error")

        if status == "skipped":
            verdict = "SKIPPED"
            skipped += 1
            layer1_issues = "No narrative text in file"
            layer2_issues = "N/A"
        elif status == "error":
            verdict = "ERROR"
            errors += 1
            layer1_issues = r.get("validation_result", "Validation error")
            layer2_issues = "N/A"
        else:
            raw_text = r.get("validation_result", "")
            verdict  = _verdict_from_text(raw_text)
            layer1_issues = _issues_from_text(raw_text)
            layer2_issues = "None"
            if verdict == "PASS":
                passed += 1
            elif verdict == "FAIL":
                failed += 1
            else:
                warned += 1

        csv_rows.append({
            "Project Name":     r.get("project_name", ""),
            "Period":           period,
            "Verdict":          verdict,
            "Layer 1 Issues":   layer1_issues,
            "Layer 2 Issues":   layer2_issues,
        })

    overall_status = "PASS" if (failed == 0 and errors == 0) else "FAIL"

    return {
        "period":         period,
        "total":          len(results),
        "passed":         passed,
        "failed":         failed,
        "warned":         warned,
        "skipped":        skipped,
        "errors":         errors,
        "overall_status": overall_status,
        "csv_rows":       csv_rows,
    }
