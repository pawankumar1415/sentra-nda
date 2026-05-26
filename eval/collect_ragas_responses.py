"""
eval/collect_ragas_responses.py  —  Phase 2 of RAGAS pipeline

Fires every question in ragas_questions.json at both deployed backends,
captures the answer + retrieved context for each, and saves the combined
dataset to ragas_dataset.json.

This script must be run AFTER deploying the backend changes that add
the `context` field to /api/chat responses.

Usage:
    cd eval
    python collect_ragas_responses.py                   # both backends
    python collect_ragas_responses.py --skip-agent      # rag only
    python collect_ragas_responses.py --skip-rag        # agent only
    python collect_ragas_responses.py --dry-run         # print questions, no API calls

Environment variables (eval/.env):
    RAG_BASE_URL        https://your-rag-function.azurewebsites.net/api
    RAG_FUNCTION_KEY    Azure Functions host key for rag_function
    RAG_JWT_TOKEN       JWT Bearer token (copy from browser DevTools → LocalStorage)
    AGENT_BASE_URL      https://your-agent-function.azurewebsites.net/api
    AGENT_FUNCTION_KEY  Azure Functions host key for agent

Output: eval/ragas_dataset.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import requests
from dotenv import load_dotenv
import os

HERE = pathlib.Path(__file__).parent
load_dotenv(HERE / ".env")

QUESTIONS_FILE = HERE / "ragas_questions.json"
OUTPUT_FILE    = HERE / "ragas_dataset.json"

DELAY_BETWEEN_CALLS = 2  # seconds — avoids rate-limit on GPT


# ── API callers ───────────────────────────────────────────────────────────────

def _call_rag_chat(question: str) -> dict:
    """POST /api/chat to the custom RAG backend."""
    base_url     = os.environ["RAG_BASE_URL"].rstrip("/")
    function_key = os.environ["RAG_FUNCTION_KEY"]
    jwt_token    = os.environ["RAG_JWT_TOKEN"]

    resp = requests.post(
        f"{base_url}/chat",
        json={"question": question},
        headers={
            "x-functions-key": function_key,
            "Authorization":   f"Bearer {jwt_token}",
            "Content-Type":    "application/json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _call_agent_chat(question: str) -> dict:
    """POST /api/chat to the Foundry/agent backend."""
    base_url     = os.environ["AGENT_BASE_URL"].rstrip("/")
    function_key = os.environ["AGENT_FUNCTION_KEY"]

    resp = requests.post(
        f"{base_url}/chat",
        json={"question": question},
        headers={
            "x-functions-key": function_key,
            "Content-Type":    "application/json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ── Main collection loop ──────────────────────────────────────────────────────

def collect(skip_rag: bool, skip_agent: bool, dry_run: bool) -> None:
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(questions)} questions from {QUESTIONS_FILE.name}")

    # Load existing dataset so we can resume if interrupted
    if OUTPUT_FILE.exists():
        existing = {r["id"]: r for r in json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))}
        print(f"Resuming — {len(existing)} entries already collected")
    else:
        existing = {}

    dataset = list(existing.values())
    processed_ids = set(existing.keys())

    for i, q in enumerate(questions, 1):
        qid      = q["id"]
        question = q["question"]
        gt       = q["ground_truth"]
        intent   = q["intent"]

        print(f"\n[{i}/{len(questions)}] {qid} ({intent})")
        print(f"  Q: {question}")

        if dry_run:
            print("  [dry-run] skipping API calls")
            continue

        if qid in processed_ids:
            print("  [skip] already collected")
            continue

        entry = {
            "id":           qid,
            "intent":       intent,
            "question":     question,
            "ground_truth": gt,
            "meta":         q.get("meta", {}),
            "rag":          None,
            "agent":        None,
        }

        # ── RAG backend ──────────────────────────────────────────────────────
        if not skip_rag:
            try:
                print("  Calling RAG backend...", end="", flush=True)
                result = _call_rag_chat(question)
                entry["rag"] = {
                    "answer":  result.get("answer", ""),
                    "context": result.get("context", ""),
                }
                print(f" OK ({len(entry['rag']['answer'])} chars answer, "
                      f"{len(entry['rag']['context'])} chars context)")
            except Exception as exc:
                print(f" ERROR: {exc}")
                entry["rag"] = {"answer": "", "context": "", "error": str(exc)}
            time.sleep(DELAY_BETWEEN_CALLS)

        # ── Agent backend ─────────────────────────────────────────────────────
        if not skip_agent:
            try:
                print("  Calling Agent backend...", end="", flush=True)
                result = _call_agent_chat(question)
                entry["agent"] = {
                    "answer":  result.get("answer", ""),
                    "context": result.get("context", ""),
                }
                print(f" OK ({len(entry['agent']['answer'])} chars answer, "
                      f"{len(entry['agent']['context'])} chars context)")
            except Exception as exc:
                print(f" ERROR: {exc}")
                entry["agent"] = {"answer": "", "context": "", "error": str(exc)}
            time.sleep(DELAY_BETWEEN_CALLS)

        dataset.append(entry)
        processed_ids.add(qid)

        # Save after every question so a crash doesn't lose progress
        OUTPUT_FILE.write_text(
            json.dumps(dataset, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if not dry_run:
        print(f"\nDone. Dataset saved to {OUTPUT_FILE}")
        rag_ok   = sum(1 for r in dataset if r.get("rag")   and not r["rag"].get("error"))
        agent_ok = sum(1 for r in dataset if r.get("agent") and not r["agent"].get("error"))
        print(f"  RAG responses:   {rag_ok}/{len(dataset)}")
        print(f"  Agent responses: {agent_ok}/{len(dataset)}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Collect RAGAS responses from both backends")
    parser.add_argument("--skip-rag",   action="store_true", help="Skip the RAG backend")
    parser.add_argument("--skip-agent", action="store_true", help="Skip the Agent backend")
    parser.add_argument("--dry-run",    action="store_true", help="Print questions without calling APIs")
    args = parser.parse_args()

    # Validate env vars unless dry-run
    if not args.dry_run:
        missing = []
        if not args.skip_rag:
            for var in ("RAG_BASE_URL", "RAG_FUNCTION_KEY", "RAG_JWT_TOKEN"):
                if not os.environ.get(var):
                    missing.append(var)
        if not args.skip_agent:
            for var in ("AGENT_BASE_URL", "AGENT_FUNCTION_KEY"):
                if not os.environ.get(var):
                    missing.append(var)
        if missing:
            print(f"ERROR: missing environment variables: {', '.join(missing)}")
            print("Set them in eval/.env — see eval/.env.example")
            sys.exit(1)

    collect(skip_rag=args.skip_rag, skip_agent=args.skip_agent, dry_run=args.dry_run)


if __name__ == "__main__":
    main()