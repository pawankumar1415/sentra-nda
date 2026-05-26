"""
eval/generate_ragas_questions.py

Generates a RAGAS-ready question/ground-truth dataset from the raw NDA Excel files
in the 'NDA Data' folder.  No agent calls needed — answers are derived directly
from the source Excel data so they represent the true expected answers.

Output: eval/ragas_questions.json
Shape:
[
  {
    "id":           "q_001",
    "intent":       "portfolio_summary" | "project_query" | "eac_query" | "rag_filter",
    "question":     "...",
    "ground_truth": "...",
    "meta": { "period": "P09", "project": "...", ... }
  },
  ...
]

Run:
    python eval/generate_ragas_questions.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
NDA_DATA    = PROJECT_DIR / "NDA Data"
OUTPUT_FILE = SCRIPT_DIR / "ragas_questions.json"

PERIOD_FILES = {
    "P07": NDA_DATA / "P07 Exec Project Summary FINAL.xlsx",
    "P08": NDA_DATA / "P08 Exec Project Summary FINAL.xlsx",
    "P09": NDA_DATA / "P09 Exec Project Summary FINAL.xlsx",
}
EAC_FILE = NDA_DATA / "lifecycle_eac_variance.xlsx"

RAG_MAP = {"G": "Green", "A": "Amber", "R": "Red"}


# ── Excel extraction ──────────────────────────────────────────────────────────

def _extract_projects(filepath: Path, period: str) -> List[Dict]:
    """
    Parse one period MPPR Excel and return a list of project dicts.
    Column layout (0-indexed, from '1) Report' sheet):
      col[1]  Project Number
      col[2]  Project Name
      col[3]  OpCo
      col[5]  DCA RAG Status (G/A/R)
      col[23] Lifetime EAC (£m)
      col[24] EAC vs Last Period (£m)
      col[28] Schedule vs Last Period (days)
      col[38] Capability & Capacity RAG (G/A/R)
    Narrative row immediately follows: col[0]=name, col[1]=narrative text.
    """
    wb   = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
    ws   = wb["1) Report"]
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    wb.close()

    projects = []
    for i, row in enumerate(rows):
        proj_num  = row[1] if len(row) > 1 else None
        proj_name = row[2] if len(row) > 2 else None

        if not (proj_num and isinstance(proj_num, str)
                and proj_name and isinstance(proj_name, str)
                and len(proj_name) > 5
                and proj_num not in ("Portfolio Summary", "Project Number ")):
            continue

        rag_raw     = row[5]  if len(row) > 5  else None
        cap_rag_raw = row[38] if len(row) > 38 else None
        eac         = row[23] if len(row) > 23 else None
        eac_vs_last = row[24] if len(row) > 24 else None
        sched_slip  = row[28] if len(row) > 28 else None

        narrative = ""
        if (i + 1 < len(rows)
                and rows[i + 1][0] == proj_name
                and rows[i + 1][1]):
            narrative = str(rows[i + 1][1])

        projects.append({
            "period":               period,
            "project_name":         proj_name.strip(),
            "rag_status":           RAG_MAP.get(str(rag_raw).strip(), str(rag_raw)),
            "cap_rag":              RAG_MAP.get(str(cap_rag_raw).strip(), str(cap_rag_raw)),
            "eac_gbp_m":            round(float(eac), 2) if isinstance(eac, (int, float)) else None,
            "eac_vs_last_gbp_m":    round(float(eac_vs_last), 3) if isinstance(eac_vs_last, (int, float)) else None,
            "schedule_vs_last_days": int(sched_slip) if isinstance(sched_slip, (int, float)) else None,
            "narrative":            narrative,
        })

    return projects


def _extract_eac_variance(filepath: Path) -> List[Dict]:
    """Parse the lifecycle EAC variance Excel."""
    wb   = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    wb.close()

    records = []
    for row in rows:
        if not row[1]:
            continue
        eac_var  = row[5]
        sched_var = row[8]
        records.append({
            "project_name":       str(row[1]).strip(),
            "period":             str(row[2]).strip() if row[2] else "",
            "eac_current_gbp":    float(row[3]) if isinstance(row[3], (int, float)) else None,
            "eac_prev_gbp":       float(row[4]) if isinstance(row[4], (int, float)) else None,
            "eac_variance_gbp":   float(eac_var)  if isinstance(eac_var,  (int, float)) else None,
            "schedule_slip_days": int(sched_var)   if isinstance(sched_var, (int, float)) else None,
        })
    return records


# ── Question builders ─────────────────────────────────────────────────────────

def _q(counter: list, intent: str, question: str, ground_truth: str, meta: Dict) -> Dict:
    counter[0] += 1
    return {
        "id":           f"q_{counter[0]:03d}",
        "intent":       intent,
        "question":     question,
        "ground_truth": ground_truth,
        "meta":         meta,
    }


def build_questions(all_projects: Dict[str, List[Dict]], eac_records: List[Dict]) -> List[Dict]:
    questions = []
    counter   = [0]  # mutable counter for closures
    q = lambda **kw: questions.append(_q(counter, **kw))

    # ── Helpers ───────────────────────────────────────────────────────────────
    def by_rag(period: str, rag: str) -> List[str]:
        return sorted(
            p["project_name"]
            for p in all_projects[period]
            if p["rag_status"] == rag
        )

    def get(period: str, name_fragment: str) -> Optional[Dict]:
        frag = name_fragment.lower()
        for p in all_projects[period]:
            if frag in p["project_name"].lower():
                return p
        return None

    # ── 1. Portfolio summary questions ────────────────────────────────────────
    for period in ("P07", "P08", "P09"):
        red_list   = by_rag(period, "Red")
        amber_list = by_rag(period, "Amber")
        green_list = by_rag(period, "Green")

        q(
            intent="portfolio_summary",
            question=f"How many projects are Red in {period}?",
            ground_truth=(
                f"In {period}, there are {len(red_list)} Red projects: "
                + (", ".join(red_list) if red_list else "none")
                + "."
            ),
            meta={"period": period, "rag": "Red"},
        )

        q(
            intent="portfolio_summary",
            question=f"Give me a summary of the portfolio status in {period}.",
            ground_truth=(
                f"In {period}, the portfolio has {len(green_list)} Green, "
                f"{len(amber_list)} Amber, and {len(red_list)} Red projects. "
                f"Red projects: {', '.join(red_list) if red_list else 'none'}. "
                f"Amber projects: {', '.join(amber_list[:5])}{'...' if len(amber_list) > 5 else ''}."
            ),
            meta={"period": period},
        )

    # Latest period overall
    latest = "P09"
    q(
        intent="portfolio_summary",
        question="What is the overall health of the NDA portfolio in the latest reporting period?",
        ground_truth=(
            f"In the latest period ({latest}), there are "
            f"{len(by_rag(latest,'Green'))} Green, "
            f"{len(by_rag(latest,'Amber'))} Amber, and "
            f"{len(by_rag(latest,'Red'))} Red projects. "
            f"Red projects include: {', '.join(by_rag(latest,'Red'))}."
        ),
        meta={"period": latest},
    )

    # ── 2. RAG colour filter questions ────────────────────────────────────────
    for period, rag in [("P07", "Red"), ("P08", "Amber"), ("P09", "Red"), ("P09", "Amber")]:
        names = by_rag(period, rag)
        q(
            intent="rag_filter",
            question=f"Which projects have a {rag} DCA status in {period}?",
            ground_truth=(
                f"The following projects have a {rag} DCA status in {period}: "
                + (", ".join(names) if names else "none")
                + "."
            ),
            meta={"period": period, "rag": rag},
        )

    # ── 3. Project-specific queries ───────────────────────────────────────────
    project_queries = [
        ("P07", "Box Encapsulation Plant",
         "What is the RAG status of Box Encapsulation Plant in P07?"),
        ("P07", "Encrypted Comms",
         "What is the DCA status of the Encrypted Communications project in P07?"),
        ("P08", "BEPPS2",
         "What is the delivery confidence assessment for BEPPS2 in P08?"),
        ("P09", "Sellafield Product & Residue Store",
         "What is the status of the Sellafield Product and Residue Store Retreatment Plant in P09?"),
        ("P09", "Data Centre",
         "What is the RAG status of the Data Centre Replacement Project in P09?"),
        ("P08", "MSSS",
         "What is the RAG status of the MSSS project in P08?"),
        ("P09", "SSEP",
         "What is the DCA status of the Sellafield Security Enhancement Programme in P09?"),
    ]

    for period, fragment, question in project_queries:
        proj = get(period, fragment)
        if not proj:
            continue
        q(
            intent="project_query",
            question=question,
            ground_truth=(
                f"In {period}, {proj['project_name']} has a DCA RAG status of "
                f"{proj['rag_status']}."
            ),
            meta={"period": period, "project": proj["project_name"]},
        )

    # Capability & Capacity RAG
    for period, fragment in [("P09", "Box Encapsulation Plant"), ("P08", "BEPPS2")]:
        proj = get(period, fragment)
        if proj and proj["cap_rag"] not in (None, "None", "nan"):
            q(
                intent="project_query",
                question=f"What is the Capability and Capacity RAG for {proj['project_name']} in {period}?",
                ground_truth=(
                    f"The Capability and Capacity RAG for {proj['project_name']} "
                    f"in {period} is {proj['cap_rag']}."
                ),
                meta={"period": period, "project": proj["project_name"], "metric": "cap_rag"},
            )

    # Cross-period RAG change
    pairs = [
        ("Box Encapsulation Plant", "P07", "P08"),
        ("BEPPS2",                  "P08", "P09"),
    ]
    for fragment, p_from, p_to in pairs:
        proj_from = get(p_from, fragment)
        proj_to   = get(p_to,   fragment)
        if not (proj_from and proj_to):
            continue
        changed = proj_from["rag_status"] != proj_to["rag_status"]
        name = proj_from["project_name"]
        q(
            intent="project_query",
            question=f"Did the RAG status of {name} change between {p_from} and {p_to}?",
            ground_truth=(
                f"The RAG status of {name} "
                + (f"changed from {proj_from['rag_status']} in {p_from} "
                   f"to {proj_to['rag_status']} in {p_to}."
                   if changed
                   else f"remained {proj_from['rag_status']} in both {p_from} and {p_to}.")
            ),
            meta={"project": name, "period_from": p_from, "period_to": p_to},
        )

    # Narrative-based question (key issues)
    narrative_queries = [
        ("P09", "Sellafield Product & Residue Store",
         "What are the key challenges facing the Sellafield Product and Residue Store Retreatment Plant in P09?"),
        ("P07", "Box Encapsulation Plant",
         "What is causing delays to the Box Encapsulation Plant in P07?"),
        ("P08", "BEPPS2",
         "What issues is BEPPS2 experiencing in P08?"),
    ]
    for period, fragment, question in narrative_queries:
        proj = get(period, fragment)
        if not proj or not proj["narrative"]:
            continue
        # Ground truth: first ~300 chars of narrative as a condensed answer signal
        snippet = proj["narrative"][:400].replace("\n", " ").strip()
        q(
            intent="project_query",
            question=question,
            ground_truth=snippet,
            meta={"period": period, "project": proj["project_name"], "type": "narrative"},
        )

    # ── 4. EAC queries ────────────────────────────────────────────────────────

    # Largest EAC movements from lifecycle file
    sorted_by_var = sorted(
        [r for r in eac_records if r["eac_variance_gbp"] is not None],
        key=lambda r: abs(r["eac_variance_gbp"]),
        reverse=True,
    )
    if sorted_by_var:
        top = sorted_by_var[0]
        q(
            intent="eac_query",
            question="Which project has the largest EAC variance in the lifecycle EAC dataset?",
            ground_truth=(
                f"{top['project_name']} has the largest EAC variance at "
                f"£{top['eac_variance_gbp']/1_000_000:.1f}m "
                f"(period {top['period']})."
            ),
            meta={"project": top["project_name"], "period": top["period"]},
        )

    # Specific project EAC variance
    eac_targets = ["Sellafield Product", "Box Encapsulation", "BEPPS2", "Data Centre"]
    for target in eac_targets:
        matches = [r for r in eac_records if target.lower() in r["project_name"].lower()]
        if not matches:
            continue
        r = matches[0]
        eac_var_m = r["eac_variance_gbp"] / 1_000_000 if r["eac_variance_gbp"] else 0
        sched     = r["schedule_slip_days"] or 0
        q(
            intent="eac_query",
            question=f"What is the EAC variance for {r['project_name']}?",
            ground_truth=(
                f"The EAC variance for {r['project_name']} in {r['period']} is "
                f"£{eac_var_m:.1f}m with a schedule variance of {sched} days."
            ),
            meta={"project": r["project_name"], "period": r["period"]},
        )

    # Schedule variance question
    biggest_slip = max(
        (r for r in eac_records if r["schedule_slip_days"]),
        key=lambda r: r["schedule_slip_days"],
        default=None,
    )
    if biggest_slip:
        q(
            intent="eac_query",
            question="Which project has the largest schedule slip in the EAC variance data?",
            ground_truth=(
                f"{biggest_slip['project_name']} has the largest schedule slip at "
                f"{biggest_slip['schedule_slip_days']} days (period {biggest_slip['period']})."
            ),
            meta={"project": biggest_slip["project_name"]},
        )

    # EAC vs last period from MPPR data (in-period movement)
    for period, fragment in [("P09", "BEPPS2"), ("P09", "Sellafield Product & Residue Store")]:
        proj = get(period, fragment)
        if not proj or proj["eac_vs_last_gbp_m"] is None:
            continue
        movement = proj["eac_vs_last_gbp_m"]
        direction = "increase" if movement > 0 else ("decrease" if movement < 0 else "no change")
        q(
            intent="eac_query",
            question=f"Did the EAC for {proj['project_name']} change in {period} compared to the previous period?",
            ground_truth=(
                f"In {period}, the EAC for {proj['project_name']} showed a "
                f"{direction} of £{abs(movement):.1f}m vs the previous period."
                if movement != 0
                else f"In {period}, the EAC for {proj['project_name']} was unchanged vs the previous period."
            ),
            meta={"period": period, "project": proj["project_name"]},
        )

    return questions


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Loading MPPR period data...")
    all_projects = {}
    for period, filepath in PERIOD_FILES.items():
        if not filepath.exists():
            print(f"  WARNING: {filepath.name} not found — skipping {period}")
            continue
        projs = _extract_projects(filepath, period)
        all_projects[period] = projs
        print(f"  {period}: {len(projs)} projects extracted")

    print("\nLoading EAC variance data...")
    eac_records = []
    if EAC_FILE.exists():
        eac_records = _extract_eac_variance(EAC_FILE)
        print(f"  {len(eac_records)} EAC variance records loaded")
    else:
        print("  WARNING: lifecycle_eac_variance.xlsx not found")

    print("\nBuilding questions...")
    questions = build_questions(all_projects, eac_records)

    # Summary by intent
    from collections import Counter
    counts = Counter(q["intent"] for q in questions)
    print(f"\n  Total questions: {len(questions)}")
    for intent, n in sorted(counts.items()):
        print(f"    {intent}: {n}")

    OUTPUT_FILE.write_text(
        json.dumps(questions, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nSaved to {OUTPUT_FILE}")

    # Print preview
    print("\n-- Preview (first question per intent) --")
    seen = set()
    for q in questions:
        if q["intent"] not in seen:
            seen.add(q["intent"])
            print(f"\n[{q['id']}] {q['intent'].upper()}")
            print(f"  Q: {q['question']}")
            print(f"  A: {q['ground_truth'][:150]}...")


if __name__ == "__main__":
    main()