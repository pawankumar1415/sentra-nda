"""
eval/compute_metrics.py  —  Phase 3

Reads ground_truth.json + results JSON files and computes RAGAS-style metrics.

Metrics computed:
  Criteria Recall    — of criteria the human marked missing, how many did the AI catch?
  Criteria Precision — of issues the AI flagged, how many are actually missing?
  F1                 — harmonic mean of Recall and Precision
  Compliance Score   — raw score from layer1 (0-10), averaged across projects
  Rewrite Faithfulness — does the rewritten narrative contain key structural markers?

Outputs a human-readable Markdown report.

Usage:
    cd eval
    python compute_metrics.py                              # baseline only
    python compute_metrics.py --run-name improved          # single run
    python compute_metrics.py --compare baseline improved  # side-by-side diff
"""

import argparse
import json
import pathlib
import re
import sys
from textwrap import wrap

HERE     = pathlib.Path(__file__).parent
GT_PATH  = HERE / "ground_truth.json"

# ── Criteria metadata ─────────────────────────────────────────────────────────

CRITERIA_ORDER = [
    "project_description",
    "project_benefit",
    "dca_rag",
    "completion_cost",
    "schedule",
    "baseline_rag",
    "baseline_movement",
    "highlights_issues",
    "cap_cap_rag",
]

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

# Keywords used to detect whether the AI flagged a criterion as missing.
# We search (case-insensitive) in each item of layer1.issues[].
# If ANY issue string contains ANY keyword for a criterion → AI flagged it.
CRITERION_ISSUE_KEYWORDS: dict[str, list[str]] = {
    "project_description": [
        "project description", "does not describe", "no description",
        "description missing", "scope not", "what the project does",
        "purpose of the project",
    ],
    "project_benefit": [
        "project benefit", "first project benefit", "benefit not",
        "no benefit", "benefit missing", "benefit statement",
        "first benefit",
    ],
    "dca_rag": [
        "dca", "delivery confidence", "dca rag", "dca status",
        "delivery confidence assessment", "dca not",
    ],
    "completion_cost": [
        "p50", "p80", "completion cost", "cost not", "cost missing",
        "sanction", "financial figure", "eac not mentioned",
        "no cost", "cost movement",
    ],
    "schedule": [
        "schedule not", "schedule missing", "schedule position",
        "completion date not", "no date", "timeline not",
        "no schedule",
    ],
    "baseline_rag": [
        "baseline rag", "baseline status", "baseline colour",
        "baseline rag missing", "baseline rag not", "baseline not mentioned",
    ],
    "baseline_movement": [
        "baseline movement", "baseline change", "baseline increase",
        "baseline decrease", "baseline escalat", "movement not",
        "baseline movement not", "change in baseline", "baseline not explained",
    ],
    "highlights_issues": [
        "highlights in period", "issues in period", "no highlights",
        "highlights missing", "highlights not", "no issues mentioned",
        "key activities not", "nothing mentioned this period",
        "period highlights", "highlights/issues",
    ],
    "cap_cap_rag": [
        "capability", "capacity", "cap/cap", "c&c", "cap rag",
        "capability and capacity not", "cap not",
    ],
}

# Faithfulness markers: structural features a well-written narrative should contain.
FAITHFULNESS_MARKERS = {
    "rag_colour":        (r"\b(green|amber|red)\b", "RAG colour word present"),
    "remains_phrasing":  (r"\b(remains|changed to)\b", '"remains" / "changed to" phrasing'),
    "cost_figure":       (r"£[\d,.]+|GBP\s*[\d,.]+|\bp50\b|\bp80\b", "Cost figure (£/P50/P80)"),
    "benefit_statement": (r"(first project benefit|project benefit is|project benefit was)", "Benefit statement present"),
    "highlights_marker": (r"(highlights in period|issues in period|key activit)", "Highlights/Issues marker"),
    "cap_cap_mention":   (r"(capability|capacity|cap\/cap|c&c rag)", "Cap/Cap RAG mentioned"),
}


# ── Detection helpers ─────────────────────────────────────────────────────────

def _ai_flagged_criterion(issues: list[str], criterion: str) -> bool:
    """Return True if any AI issue text matches a keyword for this criterion."""
    keywords = CRITERION_ISSUE_KEYWORDS.get(criterion, [])
    combined = " ".join(issues).lower()
    return any(kw.lower() in combined for kw in keywords)


def _faithfulness(rewritten: str) -> dict:
    """Score the rewritten narrative against structural faithfulness markers."""
    text  = (rewritten or "").lower()
    scores = {}
    for key, (pattern, label) in FAITHFULNESS_MARKERS.items():
        scores[key] = {
            "present": bool(re.search(pattern, text, re.IGNORECASE)),
            "label":   label,
        }
    return scores


# ── Per-run metrics ───────────────────────────────────────────────────────────

def compute_run_metrics(ground_truth: list, results: list) -> dict:
    """
    Compute all metrics for one system's results against the ground truth.

    Returns a dict with:
      per_project   — per-project scores and criterion-level TP/FP/TN/FN
      per_criterion — aggregate recall/precision/F1 per criterion
      overall       — aggregate recall, precision, F1, avg score, faithfulness
    """
    # Index results by project name
    result_map = {r["project_name"]: r for r in results}

    per_project  = []
    # Aggregate counters per criterion
    crit_tp = {c: 0 for c in CRITERIA_ORDER}
    crit_fp = {c: 0 for c in CRITERIA_ORDER}
    crit_tn = {c: 0 for c in CRITERIA_ORDER}
    crit_fn = {c: 0 for c in CRITERIA_ORDER}

    scores          = []
    faithfulness_totals = {k: 0 for k in FAITHFULNESS_MARKERS}
    faith_count     = 0

    for gt in ground_truth:
        name    = gt["project_name"]
        gt_crit = gt["ground_truth"]

        entry = result_map.get(name, {})
        status = entry.get("status", "missing")

        if status != "ok" or not entry.get("result"):
            per_project.append({
                "project_name": name,
                "status":       status,
                "score":        None,
                "verdict":      None,
                "criteria":     {},
                "faithfulness": {},
            })
            continue

        result  = entry["result"]
        layer1  = result.get("layer1", {})
        issues  = layer1.get("issues", [])
        score   = layer1.get("compliance_score")
        verdict = result.get("overall_verdict", "?")
        rewrite = result.get("rewritten_narrative", "")

        if score is not None:
            scores.append(score)

        # Faithfulness of the rewritten narrative
        faith = _faithfulness(rewrite)
        faith_count += 1
        for k, v in faith.items():
            if v["present"]:
                faithfulness_totals[k] += 1

        # Per-criterion classification
        crit_details = {}
        for criterion in CRITERIA_ORDER:
            gt_val = gt_crit.get(criterion)
            if gt_val is None:
                # N/A — skip from precision/recall calculation
                crit_details[criterion] = "N/A"
                continue

            gt_missing  = not gt_val          # True = criterion missing per human
            ai_flagged  = _ai_flagged_criterion(issues, criterion)

            if gt_missing and ai_flagged:
                label = "TP"
                crit_tp[criterion] += 1
            elif not gt_missing and ai_flagged:
                label = "FP"
                crit_fp[criterion] += 1
            elif not gt_missing and not ai_flagged:
                label = "TN"
                crit_tn[criterion] += 1
            else:  # gt_missing and not ai_flagged
                label = "FN"
                crit_fn[criterion] += 1

            crit_details[criterion] = label

        per_project.append({
            "project_name":  name,
            "status":        status,
            "score":         score,
            "verdict":       verdict,
            "n_issues":      len(issues),
            "issues":        issues,
            "criteria":      crit_details,
            "faithfulness":  {k: v["present"] for k, v in faith.items()},
        })

    # ── Per-criterion aggregates ───────────────────────────────────────────────
    per_criterion = {}
    for c in CRITERIA_ORDER:
        tp = crit_tp[c]; fp = crit_fp[c]
        tn = crit_tn[c]; fn = crit_fn[c]
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall    = tp / (tp + fn) if (tp + fn) > 0 else None
        f1        = (2 * precision * recall / (precision + recall)
                     if precision is not None and recall is not None
                     and (precision + recall) > 0 else None)
        per_criterion[c] = {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": round(precision, 3) if precision is not None else None,
            "recall":    round(recall,    3) if recall    is not None else None,
            "f1":        round(f1,        3) if f1        is not None else None,
        }

    # ── Overall aggregates ────────────────────────────────────────────────────
    total_tp = sum(crit_tp.values())
    total_fp = sum(crit_fp.values())
    total_fn = sum(crit_fn.values())

    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else None
    overall_recall    = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else None
    overall_f1        = (2 * overall_precision * overall_recall
                         / (overall_precision + overall_recall)
                         if overall_precision is not None and overall_recall is not None
                         and (overall_precision + overall_recall) > 0 else None)

    avg_score = round(sum(scores) / len(scores), 2) if scores else None

    faith_scores = {
        k: round(faithfulness_totals[k] / faith_count, 2) if faith_count > 0 else None
        for k in FAITHFULNESS_MARKERS
    }
    avg_faith = (round(sum(v for v in faith_scores.values() if v) /
                       len([v for v in faith_scores.values() if v is not None]), 2)
                 if any(v is not None for v in faith_scores.values()) else None)

    return {
        "per_project":    per_project,
        "per_criterion":  per_criterion,
        "overall": {
            "precision":          round(overall_precision, 3) if overall_precision is not None else None,
            "recall":             round(overall_recall,    3) if overall_recall    is not None else None,
            "f1":                 round(overall_f1,        3) if overall_f1        is not None else None,
            "avg_compliance_score": avg_score,
            "faithfulness_by_marker": faith_scores,
            "avg_faithfulness":   avg_faith,
        },
    }


# ── Report rendering ──────────────────────────────────────────────────────────

def _pct(v) -> str:
    return f"{v*100:.0f}%" if v is not None else "—"


def _score(v) -> str:
    return str(v) if v is not None else "—"


def render_report(run_label: str, metrics: dict) -> str:
    lines = []
    lines.append(f"# Narrative Validation Eval — {run_label}")
    lines.append("")

    overall = metrics["overall"]
    lines.append("## Overall Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Criteria Recall    | {_pct(overall['recall'])} |")
    lines.append(f"| Criteria Precision | {_pct(overall['precision'])} |")
    lines.append(f"| F1 Score           | {_pct(overall['f1'])} |")
    lines.append(f"| Avg Compliance Score (0–10) | {_score(overall['avg_compliance_score'])} |")
    lines.append(f"| Avg Rewrite Faithfulness   | {_pct(overall['avg_faithfulness'])} |")
    lines.append("")

    lines.append("## Per-Criterion Metrics")
    lines.append("")
    lines.append("| Criterion | Recall | Precision | F1 | TP | FP | FN |")
    lines.append("|-----------|--------|-----------|----|----|----|----|")
    for c in CRITERIA_ORDER:
        cm = metrics["per_criterion"][c]
        lines.append(
            f"| {CRITERIA_LABELS[c]} "
            f"| {_pct(cm['recall'])} "
            f"| {_pct(cm['precision'])} "
            f"| {_pct(cm['f1'])} "
            f"| {cm['tp']} | {cm['fp']} | {cm['fn']} |"
        )
    lines.append("")

    lines.append("## Rewrite Faithfulness Markers")
    lines.append("")
    lines.append("Shows what % of rewritten narratives contain each structural marker.")
    lines.append("")
    lines.append("| Marker | Present in rewrites |")
    lines.append("|--------|---------------------|")
    for key, (_, label) in FAITHFULNESS_MARKERS.items():
        v = overall["faithfulness_by_marker"].get(key)
        lines.append(f"| {label} | {_pct(v)} |")
    lines.append("")

    lines.append("## Per-Project Results")
    lines.append("")
    for pp in metrics["per_project"]:
        name    = pp["project_name"]
        status  = pp["status"]
        score   = pp.get("score")
        verdict = pp.get("verdict", "?")
        lines.append(f"### {name}")
        if status != "ok":
            lines.append(f"**Status:** {status}")
            lines.append("")
            continue

        lines.append(f"**Score:** {score}/10  |  **Verdict:** {verdict}  |  **Issues found:** {pp.get('n_issues', 0)}")
        lines.append("")

        # Criterion grid
        lines.append("| Criterion | Result |")
        lines.append("|-----------|--------|")
        for c in CRITERIA_ORDER:
            val = pp["criteria"].get(c, "—")
            emoji = {"TP": "✅ TP", "FP": "⚠️ FP", "TN": "✓ TN", "FN": "❌ FN", "N/A": "— N/A"}.get(val, val)
            lines.append(f"| {CRITERIA_LABELS[c]} | {emoji} |")
        lines.append("")

        # Issues list
        if pp.get("issues"):
            lines.append("**AI Issues:**")
            for iss in pp["issues"]:
                lines.append(f"- {iss}")
            lines.append("")

        # Faithfulness
        faith = pp.get("faithfulness", {})
        faith_hits = [FAITHFULNESS_MARKERS[k][1] for k, v in faith.items() if v]
        faith_miss = [FAITHFULNESS_MARKERS[k][1] for k, v in faith.items() if not v]
        if faith_hits:
            lines.append(f"**Rewrite has:** {', '.join(faith_hits)}")
        if faith_miss:
            lines.append(f"**Rewrite missing:** {', '.join(faith_miss)}")
        lines.append("")

    return "\n".join(lines)


def render_comparison(run_a: str, metrics_a: dict, run_b: str, metrics_b: dict) -> str:
    lines = []
    lines.append(f"# Eval Comparison: {run_a} → {run_b}")
    lines.append("")

    def _delta(va, vb):
        if va is None or vb is None:
            return "—"
        diff = vb - va
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff*100:.0f}pp"

    ov_a = metrics_a["overall"]
    ov_b = metrics_b["overall"]

    lines.append("## Overall Comparison")
    lines.append("")
    lines.append(f"| Metric | {run_a} | {run_b} | Δ |")
    lines.append(f"|--------|{'—'*len(run_a)}|{'—'*len(run_b)}|---|")

    def _row(label, key):
        va = ov_a.get(key); vb = ov_b.get(key)
        if isinstance(va, float) and va <= 1.0:
            return f"| {label} | {_pct(va)} | {_pct(vb)} | {_delta(va, vb)} |"
        return f"| {label} | {_score(va)} | {_score(vb)} | — |"

    lines.append(_row("Criteria Recall",    "recall"))
    lines.append(_row("Criteria Precision", "precision"))
    lines.append(_row("F1 Score",           "f1"))
    lines.append(_row("Avg Faithfulness",   "avg_faithfulness"))
    lines.append(f"| Avg Compliance Score | {_score(ov_a['avg_compliance_score'])} "
                 f"| {_score(ov_b['avg_compliance_score'])} | — |")
    lines.append("")

    lines.append("## Per-Criterion Comparison")
    lines.append("")
    lines.append(f"| Criterion | Recall {run_a} | Recall {run_b} | Δ Recall |")
    lines.append(f"|-----------|---------------|---------------|----------|")
    for c in CRITERIA_ORDER:
        ra = metrics_a["per_criterion"][c]["recall"]
        rb = metrics_b["per_criterion"][c]["recall"]
        lines.append(f"| {CRITERIA_LABELS[c]} | {_pct(ra)} | {_pct(rb)} | {_delta(ra, rb)} |")
    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def _load(run_name: str, system: str) -> list:
    path = HERE / f"results_{run_name}_{system}.json"
    if not path.exists():
        print(f"WARN: {path} not found — skipping {system} for run '{run_name}'.")
        return []
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute RAGAS-style eval metrics.")
    parser.add_argument("--run-name", default="baseline",
                        help="Run label to evaluate (default: baseline)")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"),
                        help="Compare two runs side by side, e.g. --compare baseline improved")
    parser.add_argument("--system", choices=["rag", "agent", "both"], default="both",
                        help="Which system result files to load (default: both)")
    args = parser.parse_args()

    if not GT_PATH.exists():
        print(f"ERROR: {GT_PATH} not found.\nRun extract_ground_truth.py first.", file=sys.stderr)
        sys.exit(1)

    ground_truth: list = json.loads(GT_PATH.read_text())
    systems = ["rag", "agent"] if args.system == "both" else [args.system]

    if args.compare:
        run_a, run_b = args.compare
        for system in systems:
            res_a = _load(run_a, system)
            res_b = _load(run_b, system)
            if not res_a or not res_b:
                continue
            m_a = compute_run_metrics(ground_truth, res_a)
            m_b = compute_run_metrics(ground_truth, res_b)

            # Individual reports
            for run, metrics in [(run_a, m_a), (run_b, m_b)]:
                label  = f"{run} — {system}"
                report = render_report(label, metrics)
                out    = HERE / f"report_{run}_{system}.md"
                out.write_text(report)
                print(f"Wrote {out}")

            # Comparison report
            comp   = render_comparison(f"{run_a}/{system}", m_a, f"{run_b}/{system}", m_b)
            out    = HERE / f"report_compare_{system}_{run_a}_vs_{run_b}.md"
            out.write_text(comp)
            print(f"Wrote {out}")
    else:
        for system in systems:
            results = _load(args.run_name, system)
            if not results:
                continue
            metrics = compute_run_metrics(ground_truth, results)

            label  = f"{args.run_name} — {system}"
            report = render_report(label, metrics)
            out    = HERE / f"report_{args.run_name}_{system}.md"
            out.write_text(report)
            print(f"Wrote {out}")

            # Also save raw metrics as JSON for programmatic use
            json_out = HERE / f"metrics_{args.run_name}_{system}.json"
            json_out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
            print(f"Wrote {json_out}")


if __name__ == "__main__":
    main()