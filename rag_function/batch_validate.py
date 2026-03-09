"""
rag_function/batch_validate.py — Batch RAG retrieval + GPT structured response.

Pipeline for POST /api/batch-validate:
  1. Extract list of projects and texts from the uploaded Excel file.
  2. For each project that has narrative text, call `run_validate` (single route).
  3. Aggregate the results into a single list and return it.
"""

from __future__ import annotations

import logging
from typing import Dict

from ingest import parse_excel
from validate import run_validate

logger = logging.getLogger(__name__)


def run_batch_validate(file_bytes: bytes, filename: str = "", top_k: int = 5) -> Dict:
    """
    Full batch validation pipeline without database ingestion.
    Returns the consolidated JSON validation results.

    Args:
        file_bytes: Raw Excel file bytes.
        filename:   Original filename for period extraction.
        top_k:      Number of similar project chunks to retrieve.
    """
    logger.info("Starting batch validation pipeline (filename=%s)", filename or "<none>")

    period, projects = parse_excel(file_bytes, filename=filename)
    if not projects:
        return {"status": "warning", "period": period, "message": "No project rows found", "total": 0, "results": []}

    logger.info("Batch validating %d projects from period %s", len(projects), period)

    results = []
    
    for project in projects:
        project_name = project["project_name"]
        narrative_text = project.get("narrative_text", "").strip()

        # Skip running validation if there's no narrative text provided for this period
        if not narrative_text:
            results.append({
                "project_name": project_name,
                "overall_verdict": "SKIPPED",
                "message": "No narrative text provided in Excel.",
                "layer1": {"compliance_score": "-", "issues": [], "passed": []},
                "layer2": {"eac_explained": False, "schedule_explained": False, "data_flag": "none", "issues": []},
                "_meta": {"eac_variance_m": 0, "schedule_days": 0}
            })
            continue

        try:
            val_result = run_validate(
                narrative=narrative_text,
                project_name=project_name,
                period=period,
                top_k=top_k
            )
            val_result["project_name"] = project_name
            results.append(val_result)
        except Exception as e:
            logger.exception("Validation failed for project %s: %s", project_name, e)
            results.append({
                "project_name": project_name,
                "overall_verdict": "ERROR",
                "message": f"Validation pipeline error: {e}",
                "layer1": {"compliance_score": "-", "issues": [], "passed": []},
                "layer2": {"eac_explained": False, "schedule_explained": False, "data_flag": "none", "issues": []},
                "_meta": {"eac_variance_m": 0, "schedule_days": 0}
            })

    return {
        "status": "ok",
        "period": period,
        "total": len(results),
        "results": results
    }
