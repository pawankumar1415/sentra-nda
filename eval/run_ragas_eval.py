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


def _fix_o_series_kwargs(kwargs: dict) -> dict:
    """Remove params that o-series / gpt-5+ models reject."""
    if "max_tokens" in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
    for unsupported in ("temperature", "top_p", "presence_penalty", "frequency_penalty", "logit_bias", "logprobs"):
        if unsupported in kwargs:
            del kwargs[unsupported]
    return kwargs


def _patch_sync(client):
    orig = client.chat.completions.create
    def _create(*args, **kwargs):
        return orig(*args, **_fix_o_series_kwargs(kwargs))
    client.chat.completions.create = _create
    return client


def _patch_async(client):
    orig = client.chat.completions.create
    async def _create(*args, **kwargs):
        return await orig(*args, **_fix_o_series_kwargs(kwargs))
    client.chat.completions.create = _create
    return client


def _build_llm_and_embeddings():
    """
    Build RAGAS LLM and embeddings wrappers.
    Azure OpenAI (preferred) or standard OpenAI as fallback.
    LLM  → llm_factory (ragas.llms)
    Emb  → LangchainEmbeddingsWrapper around AzureOpenAIEmbeddings / OpenAIEmbeddings
    """
    endpoint    = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key     = os.environ.get("AZURE_OPENAI_API_KEY",  "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01").strip()
    chat_dep    = os.environ.get("RAGAS_JUDGE_DEPLOYMENT", "gpt-4o").strip()
    emb_dep     = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002").strip()
    openai_key  = os.environ.get("OPENAI_API_KEY", "").strip()

    if endpoint and api_key:
        from openai import AsyncAzureOpenAI, AzureOpenAI
        from ragas.llms import llm_factory
        from ragas.embeddings import OpenAIEmbeddings

        print(f"  LLM: Azure OpenAI  deployment={chat_dep}  endpoint={endpoint[:40]}...")
        # RAGAS 0.4.3 metric.score() calls agenerate() → requires async client
        llm_client = _patch_async(AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        ))
        emb_client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        llm        = llm_factory(chat_dep, client=llm_client)
        embeddings = OpenAIEmbeddings(model=emb_dep, client=emb_client)
        return llm, embeddings

    elif openai_key:
        from openai import AsyncOpenAI, AzureOpenAI
        from ragas.llms import llm_factory
        from ragas.embeddings import OpenAIEmbeddings

        print("  LLM: Standard OpenAI (gpt-4o)")
        llm_client = AsyncOpenAI(api_key=openai_key)
        emb_client = AzureOpenAI(api_key=openai_key)
        llm        = llm_factory("gpt-4o", client=llm_client)
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small", client=emb_client)
        return llm, embeddings

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
    """Build a RAGAS EvaluationDataset from collected records."""
    from ragas.dataset_schema import SingleTurnSample, EvaluationDataset

    samples = []
    for r in records:
        b = r.get(backend)
        if not b or b.get("error") or not b.get("answer"):
            continue
        samples.append(SingleTurnSample(
            user_input=r["question"],
            response=_strip_html(b["answer"]),
            retrieved_contexts=[b.get("context") or "No context retrieved"],
            reference=r["ground_truth"],
        ))
    return EvaluationDataset(samples=samples), len(samples)


# ── Evaluation ────────────────────────────────────────────────────────────────

def run_evaluation(
    backend: str,
    records: list,
    intent_filter: Optional[str],
    llm,
    embeddings,
) -> dict:
    from ragas.metrics.collections import Faithfulness, AnswerRelevancy, ContextPrecision

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

    metrics = [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm, embeddings=embeddings),
        ContextPrecision(llm=llm),
    ]
    metric_names = ["faithfulness", "answer_relevancy", "context_precision"]

    print(f"  Running RAGAS on {n} questions...")

    # Each metric accepts only specific kwargs — derived from RAGAS 0.4.3 signatures
    _METRIC_KWARGS = {
        "faithfulness":      lambda s: {"user_input": s.user_input, "response": s.response, "retrieved_contexts": s.retrieved_contexts},
        "answer_relevancy":  lambda s: {"user_input": s.user_input, "response": s.response},
        "context_precision": lambda s: {"user_input": s.user_input, "reference": s.reference, "retrieved_contexts": s.retrieved_contexts},
    }

    def _score_all():
        buckets: dict[str, list[float]] = {k: [] for k in metric_names}
        for i, sample in enumerate(dataset.samples, 1):
            print(f"    [{i:02d}/{n}] scoring...", end="\r", flush=True)
            for metric, name in zip(metrics, metric_names):
                try:
                    kwargs = _METRIC_KWARGS[name](sample)
                    val = metric.score(**kwargs)
                    if val is not None:
                        buckets[name].append(float(val))
                except Exception as exc:
                    if i == 1:
                        print(f"\n  ERROR [{name}] sample {i}: {type(exc).__name__}: {exc}")
        print()
        return buckets

    buckets = _score_all()

    def _avg(name):
        vals = buckets.get(name, [])
        return round(sum(vals) / len(vals), 4) if vals else None

    faithfulness_score    = _avg("faithfulness")
    answer_rel_score      = _avg("answer_relevancy")
    context_prec_score    = _avg("context_precision")

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