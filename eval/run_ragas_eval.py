"""
eval/run_ragas_eval.py  —  Phase 3 of RAGAS pipeline

Reads ragas_dataset.json (produced by collect_ragas_responses.py),
runs RAGAS evaluation on both backends, and prints a side-by-side
score report.

Metrics used:
  - faithfulness       — Does the answer stay grounded in the retrieved context?
  - answer_relevancy   — Does the answer actually address the question?
  - context_precision  — Is the retrieved context relevant to the question?

Usage:
    cd eval
    pip install ragas datasets langchain-openai
    python run_ragas_eval.py
    python run_ragas_eval.py --backend rag
    python run_ragas_eval.py --backend agent
    python run_ragas_eval.py --intent project_query

Required in eval/.env (Azure OpenAI — same creds as your backends):
    AZURE_OPENAI_ENDPOINT        https://your-aoai.openai.azure.com
    AZURE_OPENAI_API_KEY         your key
    AZURE_OPENAI_API_VERSION     2024-02-01
    RAGAS_JUDGE_DEPLOYMENT       gpt-4o  (or gpt-4, gpt-35-turbo)
    AZURE_EMBEDDING_DEPLOYMENT   text-embedding-ada-002
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Optional

from dotenv import load_dotenv

HERE         = pathlib.Path(__file__).parent
DATASET_FILE = HERE / "ragas_dataset.json"
OUTPUT_FILE  = HERE / "ragas_scores.json"

load_dotenv(HERE / ".env")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _build_llm_and_embeddings():
    """
    Build LangChain Azure OpenAI wrappers for RAGAS.
    Fails fast with a clear message if credentials are missing.
    """
    endpoint    = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key     = os.environ.get("AZURE_OPENAI_API_KEY",  "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01").strip()
    chat_dep    = os.environ.get("RAGAS_JUDGE_DEPLOYMENT", "gpt-4o").strip()
    emb_dep     = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002").strip()
    openai_key  = os.environ.get("OPENAI_API_KEY", "").strip()

    if endpoint and api_key:
        from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper

        print(f"  LLM: Azure OpenAI  deployment={chat_dep}  endpoint={endpoint[:40]}...")
        llm = AzureChatOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            azure_deployment=chat_dep,
            temperature=0,
        )
        embeddings = AzureOpenAIEmbeddings(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            azure_deployment=emb_dep,
        )
        return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(embeddings)

    elif openai_key:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper

        print("  LLM: Standard OpenAI (gpt-4o)")
        llm        = ChatOpenAI(model="gpt-4o", temperature=0)
        embeddings = OpenAIEmbeddings()
        return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(embeddings)

    else:
        print("ERROR: No LLM credentials found.")
        print("Add to eval/.env:")
        print("  AZURE_OPENAI_ENDPOINT=https://your-aoai.openai.azure.com")
        print("  AZURE_OPENAI_API_KEY=your_key")
        print("  RAGAS_JUDGE_DEPLOYMENT=gpt-4o")
        print("  AZURE_EMBEDDING_DEPLOYMENT=text-embedding-ada-002")
        print("  AZURE_OPENAI_API_VERSION=2024-02-01")
        print("Or set OPENAI_API_KEY for standard OpenAI.")
        sys.exit(1)


def _build_ragas_dataset(records: list, backend: str):
    from datasets import Dataset

    rows = []
    for r in records:
        b = r.get(backend)
        if not b or b.get("error") or not b.get("answer"):
            continue
        rows.append({
            "question":     r["question"],
            "answer":       _strip_html(b["answer"]),
            "contexts":     [b.get("context") or "No context retrieved"],
            "ground_truth": r["ground_truth"],
        })
    return Dataset.from_list(rows), len(rows)


# ── Evaluation ────────────────────────────────────────────────────────────────

def run_evaluation(
    backend: str,
    records: list,
    intent_filter: Optional[str],
    llm,
    embeddings,
) -> dict:
    # Use new import path (ragas >= 0.2)
    try:
        from ragas.metrics.collections import Faithfulness, AnswerRelevancy, ContextPrecision
    except ImportError:
        from ragas.metrics import faithfulness as _f, answer_relevancy as _a, context_precision as _c
        Faithfulness     = type(_f)
        AnswerRelevancy  = type(_a)
        ContextPrecision = type(_c)

    from ragas import evaluate

    filtered = records
    if intent_filter:
        filtered = [r for r in records if r.get("intent") == intent_filter]

    label = f"[{backend.upper()}]"
    suffix = f", intent={intent_filter}" if intent_filter else ""
    print(f"\n{label} Building dataset ({len(filtered)} questions{suffix})...")

    dataset, n = _build_ragas_dataset(filtered, backend)
    if n == 0:
        print(f"  No valid responses for {backend} — skipping")
        return {}

    # Instantiate metrics with our LLM so RAGAS doesn't try to create OpenAI()
    metrics = [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm, embeddings=embeddings),
        ContextPrecision(llm=llm),
    ]

    print(f"  Running RAGAS on {n} questions...")
    result = evaluate(dataset, metrics=metrics)

    # Result keys vary slightly by version — try both naming styles
    def _get(key, alt=None):
        for k in [key, alt or key]:
            if k in result:
                return round(float(result[k]), 4)
        return None

    faithfulness_score    = _get("faithfulness")
    answer_rel_score      = _get("answer_relevancy")
    context_prec_score    = _get("context_precision")

    scores = {
        "backend":           backend,
        "n_questions":       n,
        "faithfulness":      faithfulness_score,
        "answer_relevancy":  answer_rel_score,
        "context_precision": context_prec_score,
    }
    valid = [v for v in scores.values() if isinstance(v, float)]
    scores["overall"] = round(sum(valid) / len(valid), 4) if valid else None
    return scores


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(rag_scores: dict, agent_scores: dict) -> None:
    metrics = ["faithfulness", "answer_relevancy", "context_precision", "overall"]
    col_w   = 14

    print("\n" + "=" * 62)
    print("  RAGAS EVALUATION RESULTS")
    print("=" * 62)
    print(f"  {'Metric':<22} {'Custom (RAG)':>{col_w}} {'Canvas (Agent)':>{col_w}}")
    print(f"  {'-'*22} {'-'*col_w} {'-'*col_w}")
    for m in metrics:
        rv = rag_scores.get(m)
        av = agent_scores.get(m)
        rs = f"{rv:.4f}" if isinstance(rv, float) else "-"
        as_ = f"{av:.4f}" if isinstance(av, float) else "-"
        label = m.replace("_", " ").title()
        print(f"  {label:<22} {rs:>{col_w}} {as_:>{col_w}}")
    print("=" * 62)
    rn = rag_scores.get("n_questions", 0)
    an = agent_scores.get("n_questions", 0)
    print(f"  Questions scored: Custom={rn}, Canvas={an}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation on collected responses")
    parser.add_argument("--backend", choices=["rag", "agent", "both"], default="both")
    parser.add_argument("--intent",  help="Filter by intent (e.g. project_query)")
    args = parser.parse_args()

    if not DATASET_FILE.exists():
        print(f"ERROR: {DATASET_FILE} not found. Run collect_ragas_responses.py first.")
        sys.exit(1)

    try:
        import ragas
        from datasets import Dataset
    except ImportError:
        print("ERROR: Install missing packages:  pip install ragas datasets langchain-openai")
        sys.exit(1)

    records = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {DATASET_FILE.name}")

    llm, embeddings = _build_llm_and_embeddings()

    rag_scores   = {}
    agent_scores = {}

    if args.backend in ("rag", "both"):
        rag_scores = run_evaluation("rag", records, args.intent, llm, embeddings)

    if args.backend in ("agent", "both"):
        agent_scores = run_evaluation("agent", records, args.intent, llm, embeddings)

    print_report(rag_scores, agent_scores)

    OUTPUT_FILE.write_text(json.dumps({"rag": rag_scores, "agent": agent_scores}, indent=2), encoding="utf-8")
    print(f"\nScores saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()