"""
eval/generate_business_report.py

Generates a clean, business-friendly Excel report comparing two eval runs.

One sheet per system (RAG / Agent), each with:
  Project Name | Narrative | Previous Score | New Score | Improvement

Usage:
    cd eval
    python generate_business_report.py                          # baseline only (no "New Score")
    python generate_business_report.py --before baseline --after improved
    python generate_business_report.py --before baseline --after improved --system rag
    python generate_business_report.py --before baseline --after improved --system agent

Environment:
    Reads result files produced by run_eval.py:
        results_<run>_rag.json
        results_<run>_agent.json
    Reads ground_truth.json produced by extract_ground_truth.py.
"""

import argparse
import json
import pathlib

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE    = pathlib.Path(__file__).parent
GT_PATH = HERE / "ground_truth.json"

# ── Colours ───────────────────────────────────────────────────────────────────
C_HEADER      = "1F3864"   # dark navy  — column headings
C_TITLE_BG    = "2E75B6"   # medium blue — sheet title row
C_IMPROVE_POS = "C6EFCE"   # green  — score went up
C_IMPROVE_NEG = "FFC7CE"   # red    — score went down
C_IMPROVE_NIL = "DDEBF7"   # light blue — no change
C_SCORE_HIGH  = "C6EFCE"   # green  — score >= 8
C_SCORE_MID   = "FFEB9C"   # yellow — score 6-7
C_SCORE_LOW   = "FFC7CE"   # red    — score <= 5
C_ROW_ALT     = "F2F7FB"   # very light blue — alternate row tint
C_WHITE       = "FFFFFF"
C_BORDER      = "9DC3E6"


# ── Style helpers ─────────────────────────────────────────────────────────────

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _font(bold=False, white=False, size=11, italic=False) -> Font:
    return Font(bold=bold, italic=italic, size=size,
                color="FFFFFF" if white else "000000")


def _border() -> Border:
    s = Side(border_style="thin", color=C_BORDER)
    return Border(left=s, right=s, top=s, bottom=s)


def _cell(ws, row: int, col: int, value,
          bg=C_WHITE, bold=False, white=False,
          center=False, wrap=True, size=11, italic=False):
    c = ws.cell(row=row, column=col, value=value)
    c.fill      = _fill(bg)
    c.font      = _font(bold=bold, white=white, size=size, italic=italic)
    c.border    = _border()
    c.alignment = Alignment(
        horizontal="center" if center else "left",
        vertical="center",
        wrap_text=wrap,
    )
    return c


def _score_bg(score) -> str:
    if score is None:
        return C_WHITE
    if score >= 8:
        return C_SCORE_HIGH
    if score >= 6:
        return C_SCORE_MID
    return C_SCORE_LOW


def _improve_bg(delta) -> str:
    if delta is None:
        return C_WHITE
    if delta > 0:
        return C_IMPROVE_POS
    if delta < 0:
        return C_IMPROVE_NEG
    return C_IMPROVE_NIL


def _improve_str(delta) -> str:
    if delta is None:
        return "—"
    if delta > 0:
        return f"+{delta}"
    return str(delta)


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_results(run_name: str, system: str) -> dict:
    """Return {project_name: result_entry} or {} if file missing."""
    path = HERE / f"results_{run_name}_{system}.json"
    if not path.exists():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {e["project_name"]: e for e in entries}


def _score_from_entry(entry: dict):
    """Extract layer1 compliance_score from a result entry, or None."""
    if not entry or entry.get("status") != "ok":
        return None
    result = entry.get("result") or {}
    return result.get("layer1", {}).get("compliance_score")


def _verdict_from_entry(entry: dict) -> str:
    if not entry or entry.get("status") != "ok":
        return "—"
    result = entry.get("result") or {}
    return result.get("overall_verdict", "—")


# ── Sheet builder ─────────────────────────────────────────────────────────────

def _build_sheet(wb, system: str, ground_truth: list,
                 before_name: str, after_name: str | None):
    ws = wb.create_sheet(system.upper())
    ws.sheet_view.showGridLines = False

    before_map = _load_results(before_name, system)
    after_map  = _load_results(after_name,  system) if after_name else {}

    has_after = bool(after_map)

    # ── Title row ─────────────────────────────────────────────────────────────
    n_cols = 5 if has_after else 3
    title  = (f"Narrative Validation Results — {system.upper()} — "
              f"{before_name.title()} vs {after_name.title()}"
              if has_after else
              f"Narrative Validation Results — {system.upper()} — {before_name.title()}")

    row = 1
    title_cell = ws.cell(row=row, column=1, value=title)
    title_cell.font      = Font(bold=True, size=13, color="FFFFFF")
    title_cell.fill      = _fill(C_TITLE_BG)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=n_cols)
    ws.row_dimensions[row].height = 28
    row += 1

    # ── Column headers ────────────────────────────────────────────────────────
    headers = ["Project Name", "Narrative"]
    if has_after:
        headers += ["Previous Score (/10)", "New Score (/10)", "Improvement"]
    else:
        headers += ["Score (/10)"]

    for ci, h in enumerate(headers, 1):
        _cell(ws, row, ci, h, bg=C_HEADER, bold=True, white=True,
              center=True, size=11)
    ws.row_dimensions[row].height = 36
    row += 1

    # ── Data rows ─────────────────────────────────────────────────────────────
    for i, gt in enumerate(ground_truth):
        name      = gt["project_name"]
        narrative = gt.get("original_narrative", "")
        row_bg    = C_ROW_ALT if i % 2 == 0 else C_WHITE

        before_entry = before_map.get(name, {})
        after_entry  = after_map.get(name,  {})

        score_before = _score_from_entry(before_entry)
        score_after  = _score_from_entry(after_entry) if has_after else None

        delta = None
        if has_after and score_before is not None and score_after is not None:
            delta = score_after - score_before

        # Project name
        _cell(ws, row, 1, name, bg=row_bg, bold=True)

        # Narrative text (truncated to 1000 chars so cell stays readable)
        narrative_display = narrative[:1000] + ("…" if len(narrative) > 1000 else "")
        _cell(ws, row, 2, narrative_display, bg=row_bg, italic=False)

        if has_after:
            # Previous score
            _cell(ws, row, 3,
                  score_before if score_before is not None else "—",
                  bg=_score_bg(score_before), center=True, bold=True)
            # New score
            _cell(ws, row, 4,
                  score_after if score_after is not None else "—",
                  bg=_score_bg(score_after), center=True, bold=True)
            # Improvement
            _cell(ws, row, 5,
                  _improve_str(delta),
                  bg=_improve_bg(delta), center=True, bold=True)
        else:
            _cell(ws, row, 3,
                  score_before if score_before is not None else "—",
                  bg=_score_bg(score_before), center=True, bold=True)

        ws.row_dimensions[row].height = 80
        row += 1

    # ── Column widths ─────────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 38   # Project Name
    ws.column_dimensions["B"].width = 80   # Narrative
    ws.column_dimensions["C"].width = 20   # Score / Previous Score
    if has_after:
        ws.column_dimensions["D"].width = 20  # New Score
        ws.column_dimensions["E"].width = 16  # Improvement

    # ── Legend row ────────────────────────────────────────────────────────────
    row += 1
    _cell(ws, row, 1, "Score legend:", bold=True, bg=C_WHITE)
    _cell(ws, row, 2, "8–10 = PASS",  bg=C_SCORE_HIGH, center=True)
    _cell(ws, row, 3, "6–7 = WARNING", bg=C_SCORE_MID, center=True)
    _cell(ws, row, 4 if has_after else 3, "0–5 = FAIL", bg=C_SCORE_LOW, center=True)
    if has_after:
        row += 1
        _cell(ws, row, 1, "Improvement legend:", bold=True, bg=C_WHITE)
        _cell(ws, row, 2, "Score improved",     bg=C_IMPROVE_POS, center=True)
        _cell(ws, row, 3, "No change",           bg=C_IMPROVE_NIL, center=True)
        _cell(ws, row, 4, "Score decreased",     bg=C_IMPROVE_NEG, center=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a business-friendly before/after narrative eval report."
    )
    parser.add_argument("--before",  default="baseline",
                        help="Run name for 'before' results (default: baseline)")
    parser.add_argument("--after",   default=None,
                        help="Run name for 'after' results (omit for single-run report)")
    parser.add_argument("--system",  choices=["rag", "agent", "both"], default="both",
                        help="Which system(s) to include (default: both)")
    args = parser.parse_args()

    if not GT_PATH.exists():
        raise SystemExit(f"ERROR: {GT_PATH} not found.\nRun extract_ground_truth.py first.")

    ground_truth: list = json.loads(GT_PATH.read_text(encoding="utf-8"))
    systems = ["rag", "agent"] if args.system == "both" else [args.system]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove the default empty sheet

    for system in systems:
        _build_sheet(wb, system, ground_truth, args.before, args.after)

    suffix = f"{args.before}_vs_{args.after}" if args.after else args.before
    out    = HERE / f"business_report_{suffix}.xlsx"
    wb.save(out)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()