"""
eval/extract_ground_truth.py  —  Phase 1

Reads the Good Narrative Examples Excel and produces ground_truth.json.

Each record contains:
  - project_name
  - original_narrative   (the flawed narrative we validate)
  - good_narrative       (the gold-standard rewrite from the reviewer)
  - reviewer_comments    (what the reviewer changed and why)
  - ground_truth         (Y/N/N-A mapped to True/False/None for 9 criteria)

Usage:
    cd eval
    python extract_ground_truth.py
"""

import json
import pathlib
import sys

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────

EXCEL_PATH = (
    pathlib.Path(__file__).parent.parent
    / "NDA Data"
    / "MPPR - Good narrative examples.xlsx"
)
OUT_PATH = pathlib.Path(__file__).parent / "ground_truth.json"

# ── Column positions (0-based) ────────────────────────────────────────────────

COL_PROJECT_NAME = 0    # blank header  → project name
COL_ORIGINAL     = 1    # "Original"    → narrative to validate
COL_GOOD         = 11   # "What good looks like"
COL_COMMENTS     = 12   # "Comments"

# The 9 mandatory criteria and their column indices
CRITERIA_COLS = {
    "project_description": 2,    # "Project description"
    "project_benefit":     3,    # "Project Benefit"
    "dca_rag":             4,    # "DCA Rag"
    "completion_cost":     5,    # "Completion cost"
    "schedule":            6,    # "Schedule"
    "baseline_rag":        7,    # "Baseline rag"
    "baseline_movement":   8,    # "Baseline movement"
    "highlights_issues":   9,    # "Highlights/issues"
    "cap_cap_rag":         10,   # "Cap/Cap Rag"
}

CRITERIA_LABELS = {
    "project_description": "Project Description",
    "project_benefit":     "Project Benefit",
    "dca_rag":             "DCA RAG",
    "completion_cost":     "Completion Cost (P50/P80)",
    "schedule":            "Schedule Position",
    "baseline_rag":        "Baseline RAG",
    "baseline_movement":   "Baseline Movement",
    "highlights_issues":   "Highlights / Issues",
    "cap_cap_rag":         "Cap/Cap RAG",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _yn(value) -> "bool | None":
    """Map Excel cell to True (Y), False (N), or None (N/A or blank)."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip().upper()
    if s in ("Y", "YES"):
        return True
    if s in ("N", "NO"):
        return False
    return None   # N/A or anything else


def _text(row, col: int) -> str:
    try:
        v = row.iloc[col]
        if pd.isna(v):
            return ""
        return str(v).strip()
    except Exception:
        return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def extract() -> list:
    if not EXCEL_PATH.exists():
        print(f"ERROR: Excel file not found:\n  {EXCEL_PATH}", file=sys.stderr)
        print("Update EXCEL_PATH in this script if the file has moved.", file=sys.stderr)
        sys.exit(1)

    xl    = pd.ExcelFile(EXCEL_PATH)
    sheet = xl.sheet_names[0]
    df    = pd.read_excel(xl, sheet_name=sheet, header=0)

    print(f"Sheet : '{sheet}'")
    print(f"Rows  : {len(df)}  (1 header + {len(df)} data rows)")
    print(f"Cols  : {len(df.columns)}\n")

    records = []
    for _, row in df.iterrows():
        project_name = _text(row, COL_PROJECT_NAME)
        if not project_name:
            continue

        original = _text(row, COL_ORIGINAL)
        good     = _text(row, COL_GOOD)
        comments = _text(row, COL_COMMENTS)

        ground_truth = {}
        for criterion, col_idx in CRITERIA_COLS.items():
            ground_truth[criterion] = _yn(row.iloc[col_idx])

        missing  = [k for k, v in ground_truth.items() if v is False]
        present  = [k for k, v in ground_truth.items() if v is True]
        na_list  = [k for k, v in ground_truth.items() if v is None]

        records.append({
            "project_name":       project_name,
            "original_narrative": original,
            "good_narrative":     good,
            "reviewer_comments":  comments,
            "ground_truth":       ground_truth,
            "_summary": {
                "present_count":  len(present),
                "missing_count":  len(missing),
                "na_count":       len(na_list),
                "missing_criteria": missing,
                "na_criteria":      na_list,
            },
        })

        # Console summary
        missing_labels = [CRITERIA_LABELS[m] for m in missing]
        flag = f"  ← MISSING: {', '.join(missing_labels)}" if missing else "  ✓ all criteria present"
        print(f"  {project_name}{flag}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(records)} records → {OUT_PATH}")
    return records


if __name__ == "__main__":
    extract()