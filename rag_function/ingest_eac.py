"""
rag_function/ingest_eac.py — Excel → clean → upsert to nda_eac_variance table.

Parses 'lifecycle_eac_variance.xlsx' and stores the EAC variance, schedule variance,
and calculated flags in PostgreSQL so the validate endpoint can query it quickly
without needing local files.
"""

from __future__ import annotations

import io
import logging
from typing import Dict, List, Tuple

import pandas as pd

from db import DBConnection

logger = logging.getLogger(__name__)

_EAC_THRESHOLD_LOW   = 50_000    # £50k
_EAC_THRESHOLD_HIGH  = 100_000   # £0.1m
_EAC_THRESHOLD_MAJOR = 500_000   # £0.5m


def parse_eac_excel(file_bytes: bytes) -> List[Dict]:
    """Parse the EAC variance Excel file and return a list of project dicts."""
    df = pd.read_excel(io.BytesIO(file_bytes))
    
    # Normalise column names
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("\n", "") for c in df.columns]
    logger.info("EAC Excel columns (normalised): %s", list(df.columns))

    name_col = next((c for c in df.columns if "project" in c and "name" in c), None)
    if not name_col:
        name_col = next((c for c in df.columns if "project" in c or "programme" in c or "title" in c), None)
    if not name_col:
        raise ValueError(f"Could not find project name column. Available columns: {list(df.columns)}")

    period_col = next((c for c in df.columns if "period" in c), None)

    # Smart column detection — search for columns containing these keywords
    eac_var_col = next((c for c in df.columns if "eac" in c and "variance" in c), None)
    sched_var_col = next((c for c in df.columns if "variance" in c and "day" in c), None)
    if not sched_var_col:
        sched_var_col = next((c for c in df.columns if "schedule" in c and "variance" in c), None)

    logger.info("Detected columns — EAC variance: %s, Schedule variance: %s",
                eac_var_col or "(not found)", sched_var_col or "(not found)")

    projects = []
    
    for _, row in df.iterrows():
        project_name = str(row[name_col]).strip()
        if not project_name or project_name.lower() == "nan":
            continue
            
        period = str(row[period_col]).strip() if period_col and not pd.isna(row[period_col]) else ""
        
        try:
            eac_var_m = float(row[eac_var_col]) if eac_var_col and not pd.isna(row[eac_var_col]) else 0.0
        except (TypeError, ValueError):
            eac_var_m = 0.0
            
        try:
            sched = int(float(row[sched_var_col])) if sched_var_col and not pd.isna(row[sched_var_col]) else 0
        except (TypeError, ValueError):
            sched = 0

        eac_var_pounds = eac_var_m * 1_000_000

        if abs(eac_var_pounds) >= _EAC_THRESHOLD_MAJOR:
            flag = "major"
        elif abs(eac_var_pounds) >= _EAC_THRESHOLD_HIGH:
            flag = "material"
        elif abs(eac_var_pounds) >= _EAC_THRESHOLD_LOW:
            flag = "minor"
        else:
            flag = "none"

        summary = (
            f"EAC variance: £{eac_var_m:.2f}m ({flag}). "
            f"Schedule variance: {sched} days."
        )

        projects.append({
            "project_name":           project_name,
            "period_short_name":      period,
            "eac_variance":           eac_var_pounds,
            "schedule_variance_days": sched,
            "flag":                   flag,
            "summary_text":           summary,
        })

    return projects


def run_ingest_eac(file_bytes: bytes, user_id: str = "") -> Dict:
    """Pipelines EAC excel bytes into PostgreSQL table (scoped to user_id)."""
    logger.info("Starting EAC ingest pipeline (user_id=%s)", user_id)

    projects = parse_eac_excel(file_bytes)
    if not projects:
        return {"status": "warning", "message": "No EAC rows found", "indexed": 0}

    # Composite PK is (project_name, user_id) so each user's EAC data is isolated
    upsert_sql = """
        INSERT INTO nda_eac_variance (
            project_name, user_id, period_short_name, eac_variance,
            schedule_variance_days, flag, summary_text, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (project_name, user_id) DO UPDATE SET
            period_short_name      = EXCLUDED.period_short_name,
            eac_variance           = EXCLUDED.eac_variance,
            schedule_variance_days = EXCLUDED.schedule_variance_days,
            flag                   = EXCLUDED.flag,
            summary_text           = EXCLUDED.summary_text,
            updated_at             = NOW();
    """

    with DBConnection() as conn:
        with conn.cursor() as cur:
            for p in projects:
                cur.execute(upsert_sql, (
                    p["project_name"],
                    user_id or None,
                    p["period_short_name"],
                    p["eac_variance"],
                    p["schedule_variance_days"],
                    p["flag"],
                    p["summary_text"],
                ))

    logger.info("Upserted %d projects to nda_eac_variance", len(projects))

    return {
        "status":  "ok",
        "indexed": len(projects),
    }
