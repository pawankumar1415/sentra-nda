"""
agent/tools.py — Python Function Tools that the Foundry Agent calls during a run.

These are defined as plain Python functions. The agent_runner registers them
as FunctionTool objects so the Foundry Agent can invoke them when needed.

Tools:
    1. check_eac_variance   — looks up period-over-period EAC and schedule variance
                              for a project and returns a structured assessment.
    2. get_project_context  — fetches the current narrative + RAG fields from
                              AI Search for a given project + period (used when
                              the AI Search tool isn't sufficient for structured lookup).
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
from functools import lru_cache

import pandas as pd

from config import (
    EAC_VARIANCE_FILE,
    EAC_VARIANCE_THRESHOLD_LOW,
    EAC_VARIANCE_THRESHOLD_HIGH,
    EAC_VARIANCE_THRESHOLD_MAJOR,
    SCHEDULE_VARIANCE_THRESHOLD_DAYS,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_eac_dataframe() -> pd.DataFrame:
    """Load the lifecycle EAC variance worksheet once and cache it."""
    if not EAC_VARIANCE_FILE.exists():
        raise FileNotFoundError(
            f"EAC variance file not found: {EAC_VARIANCE_FILE}\n"
            "Ensure 'NDA Data/lifecycle_eac_variance.xlsx' exists in the repo root."
        )
    df = pd.read_excel(EAC_VARIANCE_FILE, sheet_name=0)
    # Normalise project names to lowercase stripped for fuzzy matching
    df["_name_norm"] = df["Project Name"].str.strip().str.lower()
    return df


def _categorise_eac_variance(variance_gbp: float) -> dict:
    """
    Return a severity label and whether narrative comment is required.

    Thresholds (agreed with Joanne):
        < £50k    → no comment needed
        ≥ £0.1m   → must appear in narrative (NDA reports to £0.1m)
        ≥ £0.5m   → significant — explicit explanation required
    """
    abs_var = abs(variance_gbp)
    direction = "increase" if variance_gbp > 0 else "decrease"

    if abs_var < EAC_VARIANCE_THRESHOLD_LOW:
        return {
            "severity": "negligible",
            "comment_required": False,
            "label": f"£{abs_var/1_000_000:.3f}m {direction} (< £50k threshold — no comment required)",
        }
    elif abs_var < EAC_VARIANCE_THRESHOLD_HIGH:
        return {
            "severity": "minor",
            "comment_required": False,
            "label": f"£{abs_var/1_000_000:.3f}m {direction} (< £0.1m — below NDA reporting threshold)",
        }
    elif abs_var < EAC_VARIANCE_THRESHOLD_MAJOR:
        return {
            "severity": "material",
            "comment_required": True,
            "label": f"£{abs_var/1_000_000:.1f}m {direction} — MUST be acknowledged in narrative",
        }
    else:
        return {
            "severity": "significant",
            "comment_required": True,
            "label": f"£{abs_var/1_000_000:.1f}m {direction} — SIGNIFICANT. Requires explicit explanation in narrative",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: check_eac_variance
# ─────────────────────────────────────────────────────────────────────────────
def check_eac_variance(project_name: str, period_short_name: str = "") -> str:
    """
    Look up the EAC and schedule variance for a given project from the
    lifecycle EAC variance dataset and return a structured assessment.

    This is the key data-driven validation tool. Call it for any project
    where you need to check whether material movements require explanation
    in the narrative.

    Args:
        project_name:       The project name as it appears in the reporting
                            workbook (partial matches are supported).
        period_short_name:  Optional. The period identifier (e.g. "2025-P07").
                            If omitted, the most recent period in the dataset is used.

    Returns:
        A JSON string containing:
            - project_name
            - period
            - eac_current, eac_previous, eac_variance_gbp
            - eac_assessment: {severity, comment_required, label}
            - schedule_variance_days
            - schedule_assessment: {comment_required, label}
            - overall_narrative_requirement: summary string for the agent to use
    """
    try:
        df = _load_eac_dataframe()
    except FileNotFoundError as exc:
        return json.dumps({"error": str(exc)})

    # ── Fuzzy project name match ───────────────────────────────────────────
    name_norm = project_name.strip().lower()
    matches = df[df["_name_norm"].str.contains(name_norm, na=False)]

    if matches.empty:
        return json.dumps({
            "error": f"No project matching '{project_name}' found in EAC variance dataset.",
            "available_projects": df["Project Name"].dropna().tolist(),
        })

    # ── Period filter ──────────────────────────────────────────────────────
    if period_short_name:
        period_matches = matches[
            matches["Period Short Name"].astype(str).str.contains(period_short_name, na=False)
        ]
        if not period_matches.empty:
            matches = period_matches
        # else: fall through to use all matches (take most recent)

    # Take the most recent row by position
    row = matches.iloc[-1]

    eac_current  = row.get("Lifetime EAC", None)
    eac_previous = row.get("Lifetime EAC Prev Period", None)
    eac_variance = row.get("Lifetime EAC Variance", None)
    sched_variance_days = row.get("Project End Date Forecast Variance (days)", 0)
    end_date_current    = row.get("Project End Date Forecast", None)
    end_date_previous   = row.get("Project End Date Forecast Prev Period", None)
    period              = row.get("Period Short Name", "Unknown")
    year_end_acwp       = row.get("Year End ACWP", None)

    # ── EAC assessment ─────────────────────────────────────────────────────
    eac_assessment = _categorise_eac_variance(float(eac_variance or 0))

    # ── Schedule assessment ────────────────────────────────────────────────
    sched_days = int(sched_variance_days or 0)
    if sched_days > SCHEDULE_VARIANCE_THRESHOLD_DAYS:
        sched_assessment = {
            "comment_required": True,
            "label": f"End date has moved by {sched_days} day(s) — MUST be explained in narrative.",
        }
    elif sched_days < 0:
        sched_assessment = {
            "comment_required": True,
            "label": f"End date has improved by {abs(sched_days)} day(s) — should be noted in narrative.",
        }
    else:
        sched_assessment = {
            "comment_required": False,
            "label": "No schedule movement — no comment required.",
        }

    # ── Overall narrative requirement summary ──────────────────────────────
    requirements = []
    if eac_assessment["comment_required"]:
        requirements.append(f"EAC: {eac_assessment['label']}")
    if sched_assessment["comment_required"]:
        requirements.append(f"Schedule: {sched_assessment['label']}")

    if requirements:
        overall = (
            "DATA-DRIVEN VALIDATION ISSUES: The following movements must be explained in the narrative:\n"
            + "\n".join(f"  • {r}" for r in requirements)
        )
    else:
        overall = "DATA-DRIVEN VALIDATION PASSED: No material movements require specific narrative commentary."

    result = {
        "project_name": str(row.get("Project Name", project_name)),
        "period": str(period),
        "eac_current_gbp": float(eac_current) if eac_current and not _is_nan(eac_current) else None,
        "eac_previous_gbp": float(eac_previous) if eac_previous and not _is_nan(eac_previous) else None,
        "eac_variance_gbp": float(eac_variance) if eac_variance and not _is_nan(eac_variance) else None,
        "eac_assessment": eac_assessment,
        "end_date_current": str(end_date_current) if end_date_current and not _is_nan(end_date_current) else None,
        "end_date_previous": str(end_date_previous) if end_date_previous and not _is_nan(end_date_previous) else None,
        "schedule_variance_days": sched_days,
        "schedule_assessment": sched_assessment,
        "year_end_acwp_gbp": float(year_end_acwp) if year_end_acwp and not _is_nan(year_end_acwp) else None,
        "overall_narrative_requirement": overall,
    }

    return json.dumps(result, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: list_projects_with_material_movements
# ─────────────────────────────────────────────────────────────────────────────
def list_projects_with_material_movements(period_short_name: str = "") -> str:
    """
    Returns a list of all projects in the EAC variance dataset that have
    material EAC or schedule movements requiring narrative commentary.

    Use this tool for batch validation (NDA-002) to identify which projects
    PMO needs to chase for narrative updates.

    Args:
        period_short_name:  Optional period filter (e.g. "2025-P07").
                            If omitted, scans all periods.

    Returns:
        A JSON string with a list of projects requiring commentary.
    """
    try:
        df = _load_eac_dataframe()
    except FileNotFoundError as exc:
        return json.dumps({"error": str(exc)})

    if period_short_name:
        df = df[df["Period Short Name"].astype(str).str.contains(period_short_name, na=False)]

    flagged = []
    for _, row in df.iterrows():
        eac_var   = float(row.get("Lifetime EAC Variance", 0) or 0)
        sched_var = int(row.get("Project End Date Forecast Variance (days)", 0) or 0)

        eac_assess   = _categorise_eac_variance(eac_var)
        sched_needed = sched_var != 0

        if eac_assess["comment_required"] or sched_needed:
            flagged.append({
                "project_name": row.get("Project Name"),
                "period": row.get("Period Short Name"),
                "eac_variance_gbp": eac_var,
                "eac_severity": eac_assess["severity"],
                "schedule_variance_days": sched_var,
                "requires_narrative_update": True,
                "summary": f"EAC: {eac_assess['label']} | Schedule: {sched_var} days",
            })

    return json.dumps({
        "period_filter": period_short_name or "all",
        "total_flagged": len(flagged),
        "projects": flagged,
    }, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────
def _is_nan(val) -> bool:
    try:
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions for the Foundry Agent SDK
# ─────────────────────────────────────────────────────────────────────────────
# These are passed to the agent as a set — the SDK uses the docstrings and
# type hints to generate the JSON Schema that tells the LLM when/how to call them.
AGENT_TOOLS = {check_eac_variance, list_projects_with_material_movements}
