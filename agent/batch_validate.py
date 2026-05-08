"""
agent/batch_validate.py — Batch narrative validation using the AI Foundry Agent.

Pipeline for POST /api/batch-validate:
  1. Parse the uploaded MPPR Excel file to extract project names + narratives.
  2. For each project with a narrative, call validate_narrative() with a JSON
     system prompt so the agent returns structured data directly.
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
                "validation_result": "<raw agent JSON string>",
                "parsed":            { <structured dict> },
                "conversation_id":   "<uuid>"
            },
            ...
        ]
    }
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from agent_runner import validate_narrative
from guidance_loader import get_guidance_text

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Batch JSON system prompt
# Mirrors _VALIDATE_SYSTEM_TEMPLATE in pgvector_backend.py — proven to work.
# The agent returns structured JSON so we never need regex on free-form prose.
# ─────────────────────────────────────────────────────────────────────────────
_BATCH_SYSTEM_TEMPLATE = """You are the NDA Narrative Validation Agent. Validate the project narrative using two layers.

LAYER 1 - GUIDANCE & STRUCTURE:
{guidance}

LAYER 2 - DATA VALIDATION:
Use the check_eac_variance tool to look up EAC and schedule movement data for the project.
Then check whether material movements are explained in the narrative:
- EAC movement >= GBP 0.1m must be explained
- Schedule slip (positive days) must be mentioned
- RAG status change must be acknowledged

Return ONLY valid JSON — no markdown fences, no explanation, no extra text before or after:
{{
  "layer1": {{
    "compliance_score": <integer 0-10>,
    "issues": [<list of specific issue strings>],
    "passed": [<list of checks that passed>]
  }},
  "layer2": {{
    "eac_explained": <true | false | "not_applicable">,
    "schedule_explained": <true | false | "not_applicable">,
    "data_flag": <"none" | "minor" | "material" | "major">,
    "issues": [<list of data issue strings, empty list if none>]
  }},
  "rewritten_narrative": "<full rewritten narrative as a single continuous string>",
  "overall_verdict": <"PASS" | "WARN" | "FAIL">
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# Excel parser
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
    """Parse the '5a)NDA MPPR' sheet and return (period, projects)."""
    xl         = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_name = next((s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None)
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ascii_safe(text: str) -> str:
    """Replace common Unicode punctuation with ASCII equivalents."""
    return (
        str(text)
        .replace("…", "...")
        .replace("—", "-")
        .replace("–", "-")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("£", "GBP ")
    )


def _parse_agent_json(raw: str) -> Optional[Dict]:
    """Strip markdown fences and parse the agent's JSON response."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    m   = re.search(r'\{.*\}', raw, re.DOTALL)
    raw = m.group(0) if m else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _verdict_from_score(score: Optional[int]) -> str:
    if score is None:
        return "UNKNOWN"
    return "PASS" if score >= 8 else ("WARN" if score >= 6 else "FAIL")


def _join_issues(issues: List[str], max_issues: int = 5) -> str:
    if not issues:
        return "None"
    if len(issues) > max_issues:
        return "; ".join(issues[:max_issues]) + f"; ...and {len(issues) - max_issues} more"
    return "; ".join(issues)


# ─────────────────────────────────────────────────────────────────────────────
# Batch pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_batch_validate(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Full agent batch validation pipeline.

    The agent is called with a JSON system prompt so each response is
    structured and can be parsed directly — no regex extraction needed.
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

    guidance_text, _ = get_guidance_text()
    instructions     = _BATCH_SYSTEM_TEMPLATE.format(guidance=guidance_text)

    results = []
    for project in projects:
        project_name   = project["project_name"]
        narrative_text = project.get("narrative_text", "").strip()

        if not narrative_text:
            results.append({
                "project_name":      project_name,
                "status":            "skipped",
                "validation_result": "No narrative text in the Excel file for this project.",
                "parsed":            None,
                "conversation_id":   None,
                "narrative_text":    "",
            })
            continue

        try:
            val    = validate_narrative(
                project_name=project_name,
                narrative_text=narrative_text,
                period=period,
                instructions_override=instructions,
            )
            parsed = _parse_agent_json(val["validation_result"])
            if parsed is None:
                logger.warning("JSON parse failed for %s — raw: %.300s",
                               project_name, val["validation_result"])

            results.append({
                "project_name":      project_name,
                "status":            "ok",
                "validation_result": val["validation_result"],
                "parsed":            parsed,
                "conversation_id":   val["conversation_id"],
                "narrative_text":    narrative_text,
            })
        except Exception as exc:
            logger.exception("Agent validation failed for %s", project_name)
            results.append({
                "project_name":      project_name,
                "status":            "error",
                "validation_result": f"Validation error: {exc}",
                "parsed":            None,
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

def run_pa_batch_validate(file_bytes: bytes, filename: str = "") -> Dict:
    """
    Power Automate variant of batch validation.

    Runs the same validation pipeline as run_agent_batch_validate() but returns
    a flat csv_rows array plus summary counts for the email subject/body.
    """
    logger.info("Starting PA batch validation (filename=%s)", filename or "<none>")

    base    = run_agent_batch_validate(file_bytes, filename=filename)
    period  = base.get("period", "")
    results = base.get("results", [])

    csv_rows = []
    passed = failed = warned = skipped = errors = 0

    for r in results:
        status    = r.get("status", "error")
        narrative = _ascii_safe(r.get("narrative_text", ""))
        score     = None

        if status == "skipped":
            verdict       = "SKIPPED"
            skipped      += 1
            layer1_issues = "No narrative text in file"
            layer2_issues = "N/A"
            rewritten     = ""

        elif status == "error":
            verdict       = "ERROR"
            errors       += 1
            layer1_issues = _ascii_safe(r.get("validation_result", "Validation error"))
            layer2_issues = "N/A"
            rewritten     = ""

        else:
            parsed = r.get("parsed") or {}

            if not parsed:
                # JSON parse failed — flag clearly rather than silently showing empty
                verdict       = "PARSE_ERROR"
                errors       += 1
                layer1_issues = "Agent response could not be parsed — check Application Insights logs"
                layer2_issues = "N/A"
                rewritten     = ""
            else:
                l1  = parsed.get("layer1", {})
                l2  = parsed.get("layer2", {})

                score         = l1.get("compliance_score")
                verdict_raw   = parsed.get("overall_verdict", "")
                verdict       = verdict_raw if verdict_raw in ("PASS", "WARN", "FAIL") \
                                else _verdict_from_score(score)
                layer1_issues = _ascii_safe(_join_issues(l1.get("issues", [])))
                layer2_issues = _ascii_safe(_join_issues(l2.get("issues", [])))
                rewritten     = _ascii_safe(parsed.get("rewritten_narrative", ""))

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