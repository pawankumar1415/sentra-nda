"""
eval/run_eval.py  —  Phase 2

Runs all 10 ground-truth narratives through both backends and saves raw results.
Both systems are called with a function host key only (x-functions-key header) —
same as a curl request or a Power Automate flow.

Systems tested:
  rag    — rag_function  POST /api/validate   (function host key)
  agent  — agent folder  POST /api/validate   (function host key)

Output files:
  results_<run_name>_rag.json
  results_<run_name>_agent.json

Usage:
    cd eval
    cp .env.example .env          # fill in your keys and URLs
    python run_eval.py                        # baseline run
    python run_eval.py --run-name improved    # after prompt changes
    python run_eval.py --skip-agent           # rag only
    python run_eval.py --skip-rag             # agent only

Environment variables (eval/.env):
    RAG_BASE_URL        https://xxx.azurewebsites.net/api
    RAG_FUNCTION_KEY    Azure Functions host key for rag_function
    AGENT_BASE_URL      https://yyy.azurewebsites.net/api
    AGENT_FUNCTION_KEY  Azure Functions host key for agent
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent / ".env")

HERE           = pathlib.Path(__file__).parent
GT_PATH        = HERE / "ground_truth.json"
DEFAULT_PERIOD = ""    # leave empty → EAC lookup uses latest available


# ── API callers ───────────────────────────────────────────────────────────────

def _call_rag(base_url: str, function_key: str, jwt_token: str, project_name: str, narrative: str, period: str) -> dict:
    """POST /api/validate on the rag_function backend."""
    resp = requests.post(
        f"{base_url}/validate",
        json={"project_name": project_name, "narrative": narrative, "period": period},
        headers={
            "x-functions-key":  function_key,
            "Authorization":    f"Bearer {jwt_token}",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _call_agent(base_url: str, function_key: str, project_name: str, narrative: str, period: str) -> dict:
    """
    POST /api/validate on the agent backend.
    The agent wraps its result in a 'validation_result' JSON string — we unwrap it
    so all downstream code works with the same flat structure as the rag response.
    """
    resp = requests.post(
        f"{base_url}/validate",
        json={"project_name": project_name, "narrative": narrative, "period": period},
        headers={"x-functions-key": function_key},
        timeout=180,
    )
    resp.raise_for_status()
    body = resp.json()

    raw = body.get("validation_result", "")
    if isinstance(raw, str):
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = {"parse_error": True, "raw_response": raw}
    else:
        parsed = raw  # already a dict

    parsed["_conversation_id"] = body.get("conversation_id")
    return parsed


# ── Runner ────────────────────────────────────────────────────────────────────

def _run_system(system_name: str, call_fn, projects: list, out_path: pathlib.Path) -> None:
    print(f"\n{'─'*60}")
    print(f"  System: {system_name.upper()}  ({len(projects)} projects)")
    print(f"{'─'*60}")

    results = []
    for i, project in enumerate(projects, 1):
        name      = project["project_name"]
        narrative = project["original_narrative"]
        print(f"  [{i:02d}/{len(projects)}] {name}", end=" ... ", flush=True)

        if not narrative:
            print("SKIPPED (no narrative)")
            results.append({"project_name": name, "status": "skipped", "result": None, "error": "No narrative"})
            continue

        try:
            result   = call_fn(name, narrative, DEFAULT_PERIOD)
            score    = result.get("layer1", {}).get("compliance_score", "?")
            verdict  = result.get("overall_verdict", "?")
            n_issues = len(result.get("layer1", {}).get("issues", []))
            print(f"score={score}/10  verdict={verdict}  issues={n_issues}")
            results.append({"project_name": name, "status": "ok", "result": result, "error": None})
        except Exception as exc:
            print(f"ERROR — {exc}")
            results.append({"project_name": name, "status": "error", "result": None, "error": str(exc)})

        delay = 1.5 if system_name == "agent" else 0.5
        if i < len(projects):
            time.sleep(delay)

    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    ok      = sum(1 for r in results if r["status"] == "ok")
    errors  = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"\n  Done: {ok} ok  {errors} errors  {skipped} skipped")
    print(f"  Saved → {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run eval narratives through both backends.")
    parser.add_argument("--run-name",   default="baseline", help="Label for output files (default: baseline)")
    parser.add_argument("--skip-rag",   action="store_true", help="Skip rag_function system")
    parser.add_argument("--skip-agent", action="store_true", help="Skip agent system")
    args = parser.parse_args()

    if not GT_PATH.exists():
        print(f"ERROR: {GT_PATH} not found.\nRun extract_ground_truth.py first.", file=sys.stderr)
        sys.exit(1)

    projects: list = json.loads(GT_PATH.read_text())
    print(f"Loaded {len(projects)} projects from ground truth.")

    if not args.skip_rag:
        rag_base  = os.getenv("RAG_BASE_URL", "").rstrip("/")
        rag_key   = os.getenv("RAG_FUNCTION_KEY", "")
        rag_token = os.getenv("RAG_JWT_TOKEN", "")
        if not rag_base:
            print("\nWARN: RAG_BASE_URL not set in .env — skipping rag system.")
        elif not rag_token:
            print("\nWARN: RAG_JWT_TOKEN not set in .env — skipping rag system.")
            print("  Get it from: browser DevTools → Application → Local Storage → token value")
        else:
            _run_system(
                system_name="rag",
                call_fn=lambda name, narrative, period: _call_rag(rag_base, rag_key, rag_token, name, narrative, period),
                projects=projects,
                out_path=HERE / f"results_{args.run_name}_rag.json",
            )

    if not args.skip_agent:
        agent_base = os.getenv("AGENT_BASE_URL", "").rstrip("/")
        agent_key  = os.getenv("AGENT_FUNCTION_KEY", "")
        if not agent_base:
            print("\nWARN: AGENT_BASE_URL not set in .env — skipping agent system.")
        else:
            _run_system(
                system_name="agent",
                call_fn=lambda name, narrative, period: _call_agent(agent_base, agent_key, name, narrative, period),
                projects=projects,
                out_path=HERE / f"results_{args.run_name}_agent.json",
            )

    print("\nAll done.")


if __name__ == "__main__":
    main()