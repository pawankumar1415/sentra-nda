"""
eval/run_ragas_eval.py  —  Phase 3 of RAGAS pipeline

Reads ragas_dataset.json (produced by collect_ragas_responses.py),
runs RAGAS evaluation on both backends, and prints a side-by-side
score report.

Metrics used:
  - faithfulness       — Does the answer stay grounded in the retrieved context?
  - answer_relevancy   — Does the answer actually address the question?
  - context_precision  — Is the retrieved context relevant to the question?

(context_recall requires ground_truth to be a full reference answer in the
same style as the model output — our ground_truth is short factual strings,
so we skip it to avoid misleading scores.)

Usage:
    cd eval
    pip install ragas datasets
    python run_ragas_eval.py
    python run_ragas_eval.py --backend rag      # score RAG only
    python run_ragas_eval.py --backend agent    # score Agent only
    python run_ragas_eval.py --intent project_query  # filter by intent

Environment variables (eval/.env):
    OPENAI_API_KEY or AZURE_OPENAI_* — RAGAS uses these to call the judge LLM.
    If using Azure OpenAI, also set:
        AZURE_OPENAI_ENDPOINT
        AZURE_OPENAI_API_KEY
        AZURE_OPENAI_API_VERSION
        RAGAS_JUDGE_DEPLOYMENT   (e.g. gpt-4o)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import List, Optional

HERE           = pathlib.Path(__file__).parent
DATASET_FILE   = HERE / "ragas_dataset.json"
OUTPUT_FILE    = HERE / "ragas_scores.json"


def _strip_html(text: str) -> str:
    """Remove HTML tags so RAGAS judge LLM sees clean text."""
    import re
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _build_ragas_dataset(records: list, backend: str):
    """
    Build a HuggingFace Dataset from collected records for one backend.
    RAGAS expects columns: question, answer, contexts (List[str]), ground_truth.
    """
    from datasets import Dataset

    rows = []
    for r in records:
        b = r.get(backend)
        if not b or b.get("error") or not b.get("answer"):
            continue
        answer  = _strip_html(b["answer"])
        context = b.get("context") or ""
        rows.append({
            "question":     r["question"],
            "answer":       answer,
            "contexts":     [context] if context else ["No context retrieved"],
            "ground_truth": r["ground_truth"],
        })
    return Dataset.from_list(rows), len(rows)


def run_evaluation(backend: str, records: list, intent_filter: Optional[str]) -> dict:
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_precision

    filtered = records
    if intent_filter:
        filtered = [r for r in records if r.get("intent") == intent_filter]

    print(f"\n[{backend.upper()}] Building dataset ({len(filtered)} questions"
          + (f", intent={intent_filter}" if intent_filter else "") + ")...")

    dataset, n = _build_ragas_dataset(filtered, backend)
    if n == 0:
        print(f"  No valid responses for {backend} — skipping")
        return {}

    print(f"  Running RAGAS on {n} questions...")
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
    )

    scores = {
        "backend":            backend,
        "n_questions":        n,
        "faithfulness":       round(float(result["faithfulness"]), 4),
        "answer_relevancy":   round(float(result["answer_relevancy"]), 4),
        "context_precision":  round(float(result["context_precision"]), 4),
    }
    scores["overall"] = round(
        (scores["faithfulness"] + scores["answer_relevancy"] + scores["context_precision"]) / 3, 4
    )
    return scores


def print_report(rag_scores: dict, agent_scores: dict) -> None:
    metrics = ["faithfulness", "answer_relevancy", "context_precision", "overall"]
    col_w   = 12

    print("\n" + "=" * 60)
    print("  RAGAS EVALUATION RESULTS")
    print("=" * 60)
    print(f"  {'Metric':<22} {'Custom (RAG)':>{col_w}} {'Canvas (Agent)':>{col_w}}")
    print(f"  {'-'*22} {'-'*col_w} {'-'*col_w}")
    for m in metrics:
        rag_v   = f"{rag_scores.get(m, '-'):.4f}"   if isinstance(rag_scores.get(m), float)   else "-"
        agent_v = f"{agent_scores.get(m, '-'):.4f}" if isinstance(agent_scores.get(m), float) else "-"
        label   = m.replace("_", " ").title()
        print(f"  {label:<22} {rag_v:>{col_w}} {agent_v:>{col_w}}")
    print("=" * 60)
    print(f"  Questions scored: RAG={rag_scores.get('n_questions',0)}, "
          f"Agent={agent_scores.get('n_questions',0)}")


def main():
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation on collected responses")
    parser.add_argument("--backend",  choices=["rag", "agent", "both"], default="both")
    parser.add_argument("--intent",   help="Filter by intent (e.g. project_query)")
    args = parser.parse_args()

    if not DATASET_FILE.exists():
        print(f"ERROR: {DATASET_FILE} not found.")
        print("Run collect_ragas_responses.py first.")
        sys.exit(1)

    records = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {DATASET_FILE.name}")

    # Check RAGAS is installed
    try:
        import ragas
        from datasets import Dataset
    except ImportError:
        print("ERROR: ragas and/or datasets packages not installed.")
        print("Run: pip install ragas datasets")
        sys.exit(1)

    # Configure RAGAS to use Azure OpenAI if available
    _configure_ragas_llm()

    rag_scores   = {}
    agent_scores = {}

    if args.backend in ("rag", "both"):
        rag_scores = run_evaluation("rag", records, args.intent)

    if args.backend in ("agent", "both"):
        agent_scores = run_evaluation("agent", records, args.intent)

    print_report(rag_scores, agent_scores)

    # Save scores
    output = {"rag": rag_scores, "agent": agent_scores}
    OUTPUT_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nScores saved to {OUTPUT_FILE}")


def _configure_ragas_llm():
    """
    If Azure OpenAI env vars are set, configure RAGAS to use them.
    Falls back to standard OPENAI_API_KEY if not.
    """
    endpoint    = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key     = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    deployment  = os.environ.get("RAGAS_JUDGE_DEPLOYMENT", "gpt-4o")

    if endpoint and api_key:
        from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
        from ragas import evaluate
        import ragas.llms as rllm
        import ragas.embeddings as remb

        llm = AzureChatOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            azure_deployment=deployment,
        )
        embeddings = AzureOpenAIEmbeddings(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            azure_deployment=os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002"),
        )
        # Patch RAGAS defaults
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        import ragas.metrics as _m
        wrapped_llm = LangchainLLMWrapper(llm)
        wrapped_emb = LangchainEmbeddingsWrapper(embeddings)
        for metric in [_m.faithfulness, _m.answer_relevancy, _m.context_precision]:
            metric.llm = wrapped_llm
            if hasattr(metric, "embeddings"):
                metric.embeddings = wrapped_emb
        print("  Using Azure OpenAI for RAGAS judge LLM")
    elif os.environ.get("OPENAI_API_KEY"):
        print("  Using standard OpenAI for RAGAS judge LLM")
    else:
        print("WARNING: No LLM credentials found for RAGAS judge.")
        print("Set OPENAI_API_KEY or AZURE_OPENAI_* variables in eval/.env")


if __name__ == "__main__":
    main()