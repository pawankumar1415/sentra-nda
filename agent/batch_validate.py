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

import csv
import io
import logging
import re
from typing import Dict, List, Tuple, Optional

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
                "narrative_text":    "",
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
                "narrative_text":    narrative_text,
            })
        except Exception as exc:
            logger.exception("Agent validation failed for %s", project_name)
            results.append({
                "project_name":      project_name,
                "status":            "error",
                "validation_result": f"Validation error: {exc}",
                "conversation_id":   None,
                "narrative_text":    narrative_text,
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

def _ascii_safe(text: str) -> str:
    """Replace common Unicode punctuation with ASCII equivalents so CSV cells
    don't get mojibake'd when Power Automate reads them as Latin-1."""
    return (
        text
        .replace("…", "...")   # ellipsis
        .replace("—", "-")     # em dash
        .replace("–", "-")     # en dash
        .replace("‘", "'")     # left single quote
        .replace("’", "'")     # right single quote
        .replace("“", '"')     # left double quote
        .replace("”", '"')     # right double quote
        .replace("£", "GBP ")  # pound sign
    )


def _score_from_text(text: str) -> Optional[int]:
    """Extract compliance score from 'Compliance Score: N/10' pattern."""
    m = re.search(r"compliance score[:\s*]+(\d+)\s*/\s*10", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _verdict_from_text(text: str) -> str:
    """
    Derive PASS/WARN/FAIL from the agent's free-form prose.
    Uses compliance score as primary signal (most reliable);
    falls back to explicit verdict keywords.
    """
    score = _score_from_text(text)
    if score is not None:
        if score >= 8:
            return "PASS"
        elif score >= 6:
            return "WARN"
        else:
            return "FAIL"

    upper = text.upper()
    for phrase in ("OVERALL VERDICT: PASS", "VERDICT: PASS", "OVERALL: PASS"):
        if phrase in upper:
            return "PASS"
    for phrase in ("OVERALL VERDICT: FAIL", "VERDICT: FAIL", "OVERALL: FAIL"):
        if phrase in upper:
            return "FAIL"
    for phrase in ("OVERALL VERDICT: WARN", "VERDICT: WARN", "OVERALL: WARN"):
        if phrase in upper:
            return "WARN"
    return "UNKNOWN"


def _issues_from_text(text: str, max_issues: int = 5) -> str:
    """
    Extract Layer 1 issues from agent prose.
    Primary: ❌ lines; secondary: ⚠️ lines.
    Fallback: all non-passing lines from the Layer 1 section when no emoji markers found.
    """
    fail_lines: List[str] = []
    warn_lines: List[str] = []

    for line in text.split("\n"):
        s = re.sub(r"^[\-\*•]+\s*", "", line.strip()).strip()
        if not s:
            continue
        if s.startswith("❌"):
            clean = re.sub(r"^❌\s*", "", s)
            clean = re.sub(r"\*+", "", clean).strip()
            if clean:
                fail_lines.append(clean)
        elif s.startswith("⚠️"):
            clean = re.sub(r"^⚠️\s*", "", s)
            clean = re.sub(r"\*+", "", clean).strip()
            if clean:
                warn_lines.append(clean)

    issues = fail_lines if fail_lines else warn_lines

    if not issues:
        # Fallback: extract all non-passing lines from the Layer 1 section
        m = re.search(
            r"\*\*Layer 1[^\n]*\n(.+?)(?=\*\*Layer 2|\*\*Suggested|\*\*Overall|^---|\Z)",
            text, re.IGNORECASE | re.DOTALL | re.MULTILINE,
        )
        if m:
            for line in m.group(1).split("\n"):
                s = re.sub(r"^[\-\*•\d\.]+\s*", "", line.strip())
                s = re.sub(r"\*+", "", s).strip()
                if s and not s.startswith("✅") and "compliance score" not in s.lower():
                    issues.append(s)

    if not issues:
        return "None"

    if len(issues) > max_issues:
        trimmed = issues[:max_issues]
        trimmed.append(f"...and {len(issues) - max_issues} more")
        return "; ".join(trimmed)

    return "; ".join(issues)


def _layer2_issues_from_text(text: str) -> str:
    """
    Extract Layer 2 data issues from agent prose.
    The agent formats Layer 2 as:
      - EAC Movement: [value] -> NOT EXPLAINED in narrative
      - Schedule Movement: [value] -> NOT EXPLAINED in narrative
      - RAG Change: [value] -> NOT ADDRESSED
    Only lines containing NOT EXPLAINED or NOT ADDRESSED are real issues.
    """
    m = re.search(
        r"\*\*Layer 2[^\n]*\n(.+?)(?=\*\*Suggested|\*\*Overall|^---|\Z)",
        text, re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    if not m:
        return "None"

    issues = []
    for line in m.group(1).split("\n"):
        s = re.sub(r"^[\-\*•]+\s*", "", line.strip()).strip()
        s = re.sub(r"\*+", "", s).strip()
        upper = s.upper()
        if "NOT EXPLAINED" in upper or "NOT ADDRESSED" in upper:
            if s:
                issues.append(_ascii_safe(s))

    return "; ".join(issues) if issues else "None"


def _extract_rewritten_narrative(text: str) -> str:
    """Extract the Suggested Improvements section as the rewritten narrative."""
    m = re.search(
        r"\*\*Suggested Improvements[^\n]*\n(.+?)(?=\n---|\Z)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ""
    content = re.sub(r"\*+", "", m.group(1)).strip()
    # Collapse embedded newlines to spaces so CSV cell stays single-line
    content = re.sub(r"\s*\n\s*", " ", content)
    return _ascii_safe(content)


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
        score  = None  # reset each iteration so skipped/error rows show "-"

        narrative     = _ascii_safe(r.get("narrative_text", ""))

        if status == "skipped":
            verdict = "SKIPPED"
            skipped += 1
            layer1_issues = "No narrative text in file"
            layer2_issues = "N/A"
            rewritten     = ""
        elif status == "error":
            verdict = "ERROR"
            errors += 1
            layer1_issues = r.get("validation_result", "Validation error")
            layer2_issues = "N/A"
            rewritten     = ""
        else:
            raw_text = r.get("validation_result", "")
            verdict  = _verdict_from_text(raw_text)
            score    = _score_from_text(raw_text)
            layer1_issues = _ascii_safe(_issues_from_text(raw_text))
            layer2_issues = _ascii_safe(_layer2_issues_from_text(raw_text))
            rewritten     = _extract_rewritten_narrative(raw_text)
            if verdict == "PASS":
                passed += 1
            elif verdict == "FAIL":
                failed += 1
            else:
                warned += 1

        csv_rows.append({
            "Project Name":           _ascii_safe(r.get("project_name", "")),
            "Period":                 period,
            "Verdict":                verdict,
            "Compliance Score":       score if score is not None else "-",
            "Narrative Text":         narrative,
            "Layer 1 Issues":         layer1_issues,
            "Layer 2 Issues":         layer2_issues,
            "AI Rewritten Narrative": rewritten,
        })

    overall_status = "PASS" if (failed == 0 and errors == 0) else "FAIL"

    # Pre-build the CSV using Python's csv module so all fields are correctly
    # quoted regardless of commas in the text. Power Automate can use
    # csv_content directly as the email attachment / body instead of running
    # "Create CSV table" (which doesn't reliably quote comma-containing fields).
    _FIELDS = ["Project Name", "Period", "Verdict", "Compliance Score",
               "Narrative Text", "Layer 1 Issues", "Layer 2 Issues",
               "AI Rewritten Narrative"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(csv_rows)
    csv_content = buf.getvalue()

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
        "csv_content":    csv_content,
    }
