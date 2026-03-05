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

    name_col = next((c for c in df.columns if "project" in c or "programme" in c or "title" in c), None)
    if not name_col:
        raise ValueError(f"Could not find project name column. Available columns: {list(df.columns)}")

    period_col = next((c for c in df.columns if "period" in c), None)

    projects = []
    
    for _, row in df.iterrows():
        project_name = str(row[name_col]).strip()
        if not project_name or project_name.lower() == "nan":
            continue
            
        period = str(row[period_col]).strip() if period_col and not pd.isna(row[period_col]) else ""
        
        try:
            eac_var_m = float(row.get("eac_variance", 0) or 0)
        except (TypeError, ValueError):
            eac_var_m = 0.0
            
        try:
            sched = int(float(row.get("schedule_variance_days", 0) or 0))
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


def run_ingest_eac(file_bytes: bytes) -> Dict:
    """Pipelines EAC excel bytes into PostgreSQL table."""
    logger.info("Starting EAC ingest pipeline")
    
    projects = parse_eac_excel(file_bytes)
    if not projects:
        return {"status": "warning", "message": "No EAC rows found", "indexed": 0}

    upsert_sql = """
        INSERT INTO nda_eac_variance (
            project_name, period_short_name, eac_variance,
            schedule_variance_days, flag, summary_text, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (project_name) DO UPDATE SET
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
