"""
eval/run_eval.py  —  Phase 2

Runs all 10 ground-truth narratives through both backends and saves raw results.

Systems tested:
  rag    — rag_function  POST /api/validate   (JWT auth)
  agent  — agent folder  POST /api/validate   (Function host key)

Output files:
  results_<run_name>_rag.json
  results_<run_name>_agent.json

Usage:
    cd eval
    cp .env.example .env          # fill in your credentials
    python run_eval.py                        # baseline run
    python run_eval.py --run-name improved    # after prompt changes
    python run_eval.py --skip-agent           # rag only
    python run_eval.py --skip-rag             # agent only

Environment variables (eval/.env):
    RAG_BASE_URL         https://xxx.azurewebsites.net/api
    RAG_USERNAME         your username
    RAG_PASSWORD         your password
    AGENT_BASE_URL       https://yyy.azurewebsites.net/api
    AGENT_FUNCTION_KEY   Azure Functions host key
"""

import argparse
import json
import pathlib
import sys
import time

import requests
from dotenv import load_dotenv
import os

load_dotenv(pathlib.Path(__file__).parent / ".env")

HERE           = pathlib.Path(__file__).parent
GT_PATH        = HERE / "ground_truth.json"
DEFAULT_PERIOD = ""    # leave empty → EAC lookup uses latest available

# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_rag_token(base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token", "")
    if not token:
        raise ValueError(f"Login returned no token. Response: {resp.json()}")
    return token


# ── API callers ───────────────────────────────────────────────────────────────

def _call_rag(base_url: str, token: str, project_name: str, narrative: str, period: str) -> dict:
    """POST /api/validate on the rag_function backend."""
    resp = requests.post(
        f"{base_url}/validate",
        json={"project_name": project_name, "narrative": narrative, "period": period},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _call_agent(base_url: str, function_key: str, project_name: str, narrative: str, period: str) -> dict:
    """
    POST /api/validate on the agent backend.
    The agent wraps its result in a 'validation_result' JSON string — we unwrap it here
    so all downstream code works with the same flat structure.
    """
    resp = requests.post(
        f"{base_url}/validate",
        json={"project_name": project_name, "narrative": narrative, "period": period},
        headers={"x-functions-key": function_key},
        timeout=180,   # agent is slower — allow extra time
    )
    resp.raise_for_status()
    body = resp.json()

    raw = body.get("validation_result", "")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Strip any accidental markdown fences and retry
            import re
            clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError:
                parsed = {"parse_error": True, "raw_response": raw}
    else:
        parsed = raw  # already a dict

    # Preserve conversation_id for traceability
    parsed["_conversation_id"] = body.get("conversation_id")
    return parsed


# ── Runner ────────────────────────────────────────────────────────────────────

def _run_system(
    system_name: str,
    call_fn,
    projects: list,
    out_path: pathlib.Path,
) -> None:
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
            results.append({
                "project_name": name,
                "status":       "skipped",
                "result":       None,
                "error":        "No narrative text in ground truth",
            })
            continue

        try:
            result  = call_fn(name, narrative, DEFAULT_PERIOD)
            score   = result.get("layer1", {}).get("compliance_score", "?")
            verdict = result.get("overall_verdict", "?")
            n_issues = len(result.get("layer1", {}).get("issues", []))
            print(f"score={score}/10  verdict={verdict}  issues={n_issues}")
            results.append({
                "project_name": name,
                "status":       "ok",
                "result":       result,
                "error":        None,
            })
        except Exception as exc:
            print(f"ERROR — {exc}")
            results.append({
                "project_name": name,
                "status":       "error",
                "result":       None,
                "error":        str(exc),
            })

        # Gentle rate limiting between calls
        delay = 1.5 if system_name == "agent" else 0.5
        if i < len(projects):
            time.sleep(delay)

    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    ok      = sum(1 for r in results if r["status"] == "ok")
    errors  = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"\n  Done: {ok} ok  {errors} errors  {skipped} skipped")
    print(f"  Saved → {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run eval narratives through both backends."
    )
    parser.add_argument(
        "--run-name", default="baseline",
        help="Label for output files, e.g. 'baseline' or 'improved' (default: baseline)"
    )
    parser.add_argument("--skip-rag",   action="store_true", help="Skip rag_function system")
    parser.add_argument("--skip-agent", action="store_true", help="Skip agent system")
    args = parser.parse_args()

    if not GT_PATH.exists():
        print(f"ERROR: {GT_PATH} not found.\nRun extract_ground_truth.py first.", file=sys.stderr)
        sys.exit(1)

    projects: list = json.loads(GT_PATH.read_text())
    print(f"Loaded {len(projects)} projects from ground truth.")

    # ── RAG system ────────────────────────────────────────────────────────────
    if not args.skip_rag:
        rag_base = os.getenv("RAG_BASE_URL", "").rstrip("/")
        rag_user = os.getenv("RAG_USERNAME", "")
        rag_pass = os.getenv("RAG_PASSWORD", "")

        if not rag_base:
            print("\nWARN: RAG_BASE_URL not set in .env — skipping rag system.")
        else:
            print("\nAuthenticating with rag_function...")
            try:
                token = _get_rag_token(rag_base, rag_user, rag_pass)
                print("  OK.")

                def rag_call(name, narrative, period):
                    return _call_rag(rag_base, token, name, narrative, period)

                _run_system(
                    system_name="rag",
                    call_fn=rag_call,
                    projects=projects,
                    out_path=HERE / f"results_{args.run_name}_rag.json",
                )
            except Exception as exc:
                print(f"  Auth FAILED: {exc}\n  Skipping rag system.")

    # ── Agent system ──────────────────────────────────────────────────────────
    if not args.skip_agent:
        agent_base = os.getenv("AGENT_BASE_URL", "").rstrip("/")
        agent_key  = os.getenv("AGENT_FUNCTION_KEY", "")

        if not agent_base:
            print("\nWARN: AGENT_BASE_URL not set in .env — skipping agent system.")
        else:
            def agent_call(name, narrative, period):
                return _call_agent(agent_base, agent_key, name, narrative, period)

            _run_system(
                system_name="agent",
                call_fn=agent_call,
                projects=projects,
                out_path=HERE / f"results_{args.run_name}_agent.json",
            )

    print("\nAll done.")


if __name__ == "__main__":
    main()