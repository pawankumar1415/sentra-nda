"""
eval/collect_ragas_responses.py  —  Phase 2 of RAGAS pipeline

Uploads all NDA Data files to both backends (ingest), then fires every
question in ragas_questions.json at both backends to collect answers and
retrieved context. Saves the combined dataset to ragas_dataset.json.

Usage:
    cd eval
    python collect_ragas_responses.py                   # ingest + collect, both backends
    python collect_ragas_responses.py --skip-ingest     # skip upload, collect only
    python collect_ragas_responses.py --skip-agent      # rag backend only
    python collect_ragas_responses.py --skip-rag        # agent backend only
    python collect_ragas_responses.py --dry-run         # print plan, no API calls

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
import os
import pathlib
import sys
import time

import requests
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).parent
load_dotenv(HERE / ".env")

QUESTIONS_FILE = HERE / "ragas_questions.json"
OUTPUT_FILE    = HERE / "ragas_dataset.json"
NDA_DATA_DIR   = HERE.parent / "NDA Data"

# Files to ingest — order matters (periods first, EAC last)
MPPR_FILES = [
    NDA_DATA_DIR / "P07 Exec Project Summary FINAL.xlsx",
    NDA_DATA_DIR / "P08 Exec Project Summary FINAL.xlsx",
    NDA_DATA_DIR / "P09 Exec Project Summary FINAL.xlsx",
]
EAC_FILE = NDA_DATA_DIR / "lifecycle_eac_variance.xlsx"

DELAY_BETWEEN_CALLS = 2  # seconds between chat calls to avoid GPT rate-limit


# ── Ingest helpers ────────────────────────────────────────────────────────────

def _ingest_rag_mppr(filepath: pathlib.Path) -> dict:
    """POST /api/ingest (RAG backend) — multipart file upload."""
    base_url     = os.environ["RAG_BASE_URL"].rstrip("/")
    function_key = os.environ["RAG_FUNCTION_KEY"]
    jwt_token    = os.environ["RAG_JWT_TOKEN"]

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{base_url}/ingest",
            files={"file": (filepath.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={
                "x-functions-key": function_key,
                "Authorization":   f"Bearer {jwt_token}",
            },
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()


def _ingest_rag_eac(filepath: pathlib.Path) -> dict:
    """POST /api/ingest-eac (RAG backend)."""
    base_url     = os.environ["RAG_BASE_URL"].rstrip("/")
    function_key = os.environ["RAG_FUNCTION_KEY"]
    jwt_token    = os.environ["RAG_JWT_TOKEN"]

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{base_url}/ingest-eac",
            files={"file": (filepath.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={
                "x-functions-key": function_key,
                "Authorization":   f"Bearer {jwt_token}",
            },
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


def _ingest_agent_mppr(filepath: pathlib.Path) -> dict:
    """POST /api/pgvector/ingest-mppr (Agent backend)."""
    base_url     = os.environ["AGENT_BASE_URL"].rstrip("/")
    function_key = os.environ["AGENT_FUNCTION_KEY"]

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{base_url}/pgvector/ingest-mppr",
            files={"file": (filepath.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"x-functions-key": function_key},
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()


def _ingest_agent_eac(filepath: pathlib.Path) -> dict:
    """POST /api/pgvector/ingest-eac (Agent backend)."""
    base_url     = os.environ["AGENT_BASE_URL"].rstrip("/")
    function_key = os.environ["AGENT_FUNCTION_KEY"]

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{base_url}/pgvector/ingest-eac",
            files={"file": (filepath.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"x-functions-key": function_key},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


def _upload_file(label: str, filepath: pathlib.Path, fn) -> bool:
    """Upload one file, print result. Returns True on success."""
    print(f"    {filepath.name} ... ", end="", flush=True)
    try:
        result = fn(filepath)
        upserted = result.get("upserted", result.get("rows_upserted", result.get("count", "?")))
        print(f"OK  ({upserted} records)")
        return True
    except requests.HTTPError as exc:
        print(f"FAILED  HTTP {exc.response.status_code}: {exc.response.text[:120]}")
        return False
    except Exception as exc:
        print(f"FAILED  {exc}")
        return False


# ── Ingest phase ──────────────────────────────────────────────────────────────

def run_ingest(skip_rag: bool, skip_agent: bool) -> None:
    print("\n" + "=" * 60)
    print("  PHASE 1 — INGESTING NDA DATA FILES")
    print("=" * 60)

    # Verify all files exist before starting
    all_files = MPPR_FILES + [EAC_FILE]
    missing = [f for f in all_files if not f.exists()]
    if missing:
        print("ERROR: The following source files were not found:")
        for f in missing:
            print(f"  {f}")
        print(f"Expected location: {NDA_DATA_DIR}")
        sys.exit(1)

    if not skip_rag:
        print("\n  RAG backend (PostgreSQL / pgvector):")
        for filepath in MPPR_FILES:
            _upload_file("rag-mppr", filepath, _ingest_rag_mppr)
        _upload_file("rag-eac", EAC_FILE, _ingest_rag_eac)

    if not skip_agent:
        print("\n  Agent backend (Azure AI Search):")
        for filepath in MPPR_FILES:
            _upload_file("agent-mppr", filepath, _ingest_agent_mppr)
        _upload_file("agent-eac", EAC_FILE, _ingest_agent_eac)

    print("\n  Ingest complete. Waiting 5s for indexes to settle...")
    time.sleep(5)


# ── Chat callers ──────────────────────────────────────────────────────────────

def _call_rag_chat(question: str) -> dict:
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
    """POST /api/pgvector/chat (Agent backend)."""
    base_url     = os.environ["AGENT_BASE_URL"].rstrip("/")
    function_key = os.environ["AGENT_FUNCTION_KEY"]

    resp = requests.post(
        f"{base_url}/pgvector/chat",
        json={"question": question},
        headers={
            "x-functions-key": function_key,
            "Content-Type":    "application/json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ── Collection phase ──────────────────────────────────────────────────────────

def run_collect(skip_rag: bool, skip_agent: bool) -> None:
    print("\n" + "=" * 60)
    print("  PHASE 2 — COLLECTING ANSWERS")
    print("=" * 60)

    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    print(f"\n  Loaded {len(questions)} questions from {QUESTIONS_FILE.name}")

    # Resume support — skip already-collected questions
    if OUTPUT_FILE.exists():
        existing = {r["id"]: r for r in json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))}
        print(f"  Resuming — {len(existing)} entries already collected, {len(questions) - len(existing)} remaining")
    else:
        existing = {}

    dataset       = list(existing.values())
    processed_ids = set(existing.keys())

    for i, q in enumerate(questions, 1):
        qid      = q["id"]
        question = q["question"]
        intent   = q["intent"]

        print(f"\n  [{i:02d}/{len(questions)}] {qid} ({intent})")
        print(f"  Q: {question}")

        if qid in processed_ids:
            print("  [skip] already collected")
            continue

        entry = {
            "id":           qid,
            "intent":       intent,
            "question":     question,
            "ground_truth": q["ground_truth"],
            "meta":         q.get("meta", {}),
            "rag":          None,
            "agent":        None,
        }

        if not skip_rag:
            try:
                print("    RAG backend   ... ", end="", flush=True)
                result = _call_rag_chat(question)
                entry["rag"] = {
                    "answer":  result.get("answer", ""),
                    "context": result.get("context", ""),
                }
                print(f"OK  ({len(entry['rag']['answer'])} chars)")
            except Exception as exc:
                print(f"ERROR: {exc}")
                entry["rag"] = {"answer": "", "context": "", "error": str(exc)}
            time.sleep(DELAY_BETWEEN_CALLS)

        if not skip_agent:
            try:
                print("    Agent backend ... ", end="", flush=True)
                result = _call_agent_chat(question)
                entry["agent"] = {
                    "answer":  result.get("answer", ""),
                    "context": result.get("context", ""),
                }
                print(f"OK  ({len(entry['agent']['answer'])} chars)")
            except Exception as exc:
                print(f"ERROR: {exc}")
                entry["agent"] = {"answer": "", "context": "", "error": str(exc)}
            time.sleep(DELAY_BETWEEN_CALLS)

        dataset.append(entry)
        processed_ids.add(qid)

        # Save after every question — crash-safe
        OUTPUT_FILE.write_text(
            json.dumps(dataset, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    rag_ok   = sum(1 for r in dataset if r.get("rag")   and not r["rag"].get("error"))
    agent_ok = sum(1 for r in dataset if r.get("agent") and not r["agent"].get("error"))
    print(f"\n  Done. {len(dataset)} questions collected.")
    print(f"  RAG:   {rag_ok}/{len(dataset)} successful")
    print(f"  Agent: {agent_ok}/{len(dataset)} successful")
    print(f"  Dataset saved to {OUTPUT_FILE}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest NDA data then collect RAGAS responses")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip file upload, go straight to Q&A")
    parser.add_argument("--skip-rag",    action="store_true", help="Skip the RAG backend entirely")
    parser.add_argument("--skip-agent",  action="store_true", help="Skip the Agent backend entirely")
    parser.add_argument("--dry-run",     action="store_true", help="Print the plan without making any API calls")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — no API calls will be made\n")
        print("Files that would be ingested:")
        for f in MPPR_FILES + [EAC_FILE]:
            status = "OK" if f.exists() else "MISSING"
            print(f"  [{status}] {f.name}")
        questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        print(f"\n{len(questions)} questions would be collected from both backends.")
        return

    # Validate env vars
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

    if not args.skip_ingest:
        run_ingest(skip_rag=args.skip_rag, skip_agent=args.skip_agent)

    run_collect(skip_rag=args.skip_rag, skip_agent=args.skip_agent)


if __name__ == "__main__":
    main()