"""
agent/chat.py — Conversational chat pipeline for the Azure AI Foundry approach.

Mirrors rag_function/chat.py but uses:
  - Azure AI Search instead of PGVector for context retrieval
  - Azure Blob Storage instead of PostgreSQL for session memory
  - The same OpenAI client already used in agent_runner.py

Session memory
──────────────
Sessions stored as blobs at:
    <container>/conversations/<session_id>.json
Shape: {"messages": [{"role": "user"|"assistant", "content": "..."}]}

The session_id is a UUID the client stores in localStorage and sends back
on subsequent calls — identical pattern to the custom approach.

Response shape is identical to rag_function/chat.py so the same frontend
ChatView works for both backends without changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from azure.search.documents import SearchClient

from agent_runner import (
    _get_project_client,
    _load_conversation_history,
    _save_conversation_history,
)
from ingest_helper import SEARCH_ENDPOINT, SEARCH_INDEX_NAME, _credential
from tools import check_eac_variance

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 20

# ─── System prompts ───────────────────────────────────────────────────────────

_INTENT_PROMPT = """You are a router. Analyze the user's question about the NDA nuclear decommissioning portfolio and determine the intent.
Output ONLY a JSON object with two keys:
1. "intent": string. Choose from:
   - "portfolio_summary" (asking about overall health, count of red/amber projects, general portfolio status)
   - "project_query" (asking about a specific project or list of projects)
   - "eac_query" (asking specifically about financial movements, EAC, or schedule delays)
   - "general" (greetings, unrelated questions)
2. "project_names": list of strings. If specific projects are mentioned, list them. Otherwise empty list.
"""

_ANSWER_PROMPT = """You are the Sentra RAG Assistant, an expert AI analyzing the NDA (Nuclear Decommissioning Authority) portfolio.
You have been provided with data extracted directly from the MPPR (Major Projects Performance Report) and EAC (Estimate at Completion) variances.

Answer the user's question based strictly on the provided Context Data.
If the context data does not contain the answer, say "I don't have enough data to answer that." Do not make up financial figures or project statuses.

Formatting rules:
- Always respond in valid HTML, not Markdown.
- Use <p style="margin:0 0 4px 0"> for paragraphs.
- Use <strong> for bold text.
- Use <ul style="margin:4px 0;padding-left:18px"> and <li style="margin:2px 0"> for bullet lists.
- For tables use: <table style="border-collapse:collapse;width:100%;margin:4px 0"> with <th style="border:1px solid #ccc;padding:4px 8px;text-align:left;background:#f5f5f5"> and <td style="border:1px solid #ccc;padding:4px 8px">.
- Do NOT use <br> tags or empty <p> tags to add spacing. Use the margin styles above instead.
- Do NOT use <h1>, <h2>, <h3> headings — use <p><strong>Title</strong></p> instead.
- If summarizing multiple projects, use an HTML table with columns for Project, Status, and relevant details.
- Always mention the Reporting Period (e.g., "In P07...") if it is in the data.
- Keep answers professional, concise, and analytical.
- Do not add suggestions like "If you would like, I can also..." at the end of responses.
- Do not repeat information already stated. Answer directly and stop.
- Do not wrap the response in ```html``` code blocks. Return raw HTML only.
"""


# ─── Search helpers ───────────────────────────────────────────────────────────

def _search_client() -> SearchClient:
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX_NAME,
        credential=_credential,
    )


def _fmt_m(value) -> str:
    """Format a number as £Xm, handling None gracefully."""
    try:
        return f"£{float(value) / 1_000_000:.2f}m"
    except (TypeError, ValueError):
        return "N/A"


def _get_portfolio_summary() -> str:
    """
    Fetch all projects from the latest indexed reporting period.
    Returns a text block summarising RAG status, EAC and schedule data.
    """
    client = _search_client()

    # Fetch up to 50 docs — get the latest period by sorting ReportingPeriod desc
    results = list(client.search(
        search_text="*",
        select=["ProjectName", "ReportingPeriod", "DCA_RAG_Status",
                "EAC_vs_Last_Period", "Timeline_Delay_Days", "EAC_Cost"],
        order_by=["ReportingPeriod desc"],
        top=50,
    ))

    if not results:
        return "No portfolio data available in Azure AI Search."

    # Use the most recent period present in the results
    latest_period = results[0].get("ReportingPeriod", "Unknown")

    # Keep only the latest period documents
    period_docs = [r for r in results if r.get("ReportingPeriod") == latest_period]

    context = f"LATEST PORTFOLIO DATA (Period: {latest_period}):\n"
    for doc in period_docs:
        name      = doc.get("ProjectName", "Unknown")
        rag       = doc.get("DCA_RAG_Status", "N/A")
        eac_delta = _fmt_m(doc.get("EAC_vs_Last_Period"))
        delay     = doc.get("Timeline_Delay_Days") or 0
        context  += f"- {name}: RAG={rag}, EAC Movement={eac_delta}, Timeline Delay={delay} days\n"

    return context


def _get_project_context(question: str, project_names: List[str], top_k: int = 5) -> str:
    """
    Search Azure AI Search for narratives and financial data related to the question.
    If project names are detected, biases the search query towards them.
    """
    client = _search_client()

    search_query = question
    if project_names:
        search_query = " ".join(project_names) + " " + question

    results = list(client.search(
        search_text=search_query,
        search_fields=["ProjectName", "NarrativeText"],
        select=["ProjectName", "ReportingPeriod", "DCA_RAG_Status",
                "EAC_vs_Last_Period", "Timeline_Delay_Days", "NarrativeText"],
        top=top_k,
    ))

    if not results:
        return "No matching project data found in Azure AI Search."

    context = "RETRIEVED PROJECT CONTEXT:\n"
    for doc in results:
        name      = doc.get("ProjectName", "Unknown")
        period    = doc.get("ReportingPeriod", "N/A")
        rag       = doc.get("DCA_RAG_Status", "N/A")
        eac_delta = _fmt_m(doc.get("EAC_vs_Last_Period"))
        delay     = doc.get("Timeline_Delay_Days") or 0
        narrative = (doc.get("NarrativeText") or "")[:600]  # trim long narratives
        context  += (
            f"--- {name} ({period}) | RAG={rag} | EAC Δ={eac_delta} | Delay={delay}d ---\n"
            f"{narrative}\n\n"
        )

    return context


def _get_eac_context(project_names: List[str]) -> str:
    """
    Fetch financial-focused data for EAC queries — filters by project name if supplied,
    otherwise returns the projects with the largest EAC movements.
    """
    client = _search_client()

    search_query = " ".join(project_names) if project_names else "*"
    results = list(client.search(
        search_text=search_query,
        search_fields=["ProjectName"],
        select=["ProjectName", "ReportingPeriod", "EAC_Cost", "EAC_vs_Last_Period",
                "Baseline_Cost_P50", "Timeline_Delay_Days", "NarrativeText"],
        order_by=["EAC_vs_Last_Period desc"],
        top=10,
    ))

    if not results:
        return "No EAC data found in Azure AI Search."

    context = "EAC & SCHEDULE DATA:\n"
    for doc in results:
        name      = doc.get("ProjectName", "Unknown")
        period    = doc.get("ReportingPeriod", "N/A")
        eac       = _fmt_m(doc.get("EAC_Cost"))
        eac_delta = _fmt_m(doc.get("EAC_vs_Last_Period"))
        baseline  = _fmt_m(doc.get("Baseline_Cost_P50"))
        delay     = doc.get("Timeline_Delay_Days") or 0
        context  += (
            f"- {name} ({period}): EAC={eac}, Δ vs last period={eac_delta}, "
            f"Baseline P50={baseline}, Delay={delay} days\n"
        )

    return context


# ─── EAC blob context ────────────────────────────────────────────────────────

def _get_eac_blob_context(project_names: List[str]) -> str:
    """
    Fetch EAC variance data from Blob Storage for the detected projects.
    This is the authoritative EAC source — the lifecycle_eac_variance.xlsx file
    uploaded via /api/ingest-eac. More complete than the AI Search EAC fields
    which come from the MPPR and are often null.
    """
    if not project_names:
        return ""

    parts = []
    for name in project_names[:3]:  # cap at 3 to keep context size reasonable
        try:
            raw  = check_eac_variance(project_name=name)
            data = json.loads(raw)
            if "error" in data:
                logger.debug("EAC blob: no data for '%s': %s", name, data["error"])
                continue
            eac_var  = data.get("eac_variance_gbp") or 0
            sched    = data.get("schedule_variance_days", 0)
            parts.append(
                f"EAC DATA — {data.get('project_name', name)} (Period: {data.get('period', 'N/A')}):\n"
                f"  EAC Current:          £{(data.get('eac_current_gbp') or 0) / 1_000_000:.2f}m\n"
                f"  EAC vs Last Period:   £{eac_var / 1_000_000:.3f}m "
                f"— {data.get('eac_assessment', {}).get('label', 'N/A')}\n"
                f"  Schedule Variance:    {sched} days "
                f"— {data.get('schedule_assessment', {}).get('label', 'N/A')}\n"
                f"  Overall Requirement: {data.get('overall_narrative_requirement', 'N/A')}"
            )
        except Exception as exc:
            logger.warning("EAC blob lookup failed for '%s': %s", name, exc)

    if not parts:
        return ""
    return "EAC VARIANCE DATA (lifecycle dataset — authoritative):\n" + "\n\n".join(parts)


# ─── Intent detection ─────────────────────────────────────────────────────────

def _detect_intent(openai_client, deployment: str, question: str) -> Dict[str, Any]:
    try:
        resp = openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": _INTENT_PROMPT},
                {"role": "user",   "content": question},
            ],
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "intent":        result.get("intent", "general"),
            "project_names": result.get("project_names", []),
        }
    except Exception as exc:
        logger.error("Intent detection failed: %s", exc)
        return {"intent": "project_query", "project_names": []}


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_chat(
    question: str,
    session_id: Optional[str] = None,
) -> Dict:
    """
    Main entry point for POST /api/chat (foundry approach).

    Flow:
      1. Resolve session — load from Blob Storage or create new UUID.
      2. Detect intent from the question.
      3. Gather context from Azure AI Search.
      4. Build messages (system + history + augmented user message).
      5. Call Chat Completions and get the answer.
      6. Persist the exchange back to Blob Storage.
      7. Return answer + session_id.

    Args:
        question:   The user's question for this turn.
        session_id: UUID of an existing session. None = start fresh.

    Returns:
        Dict with keys:
            "answer"     — markdown-formatted response
            "session_id" — UUID the client should persist
            "meta"       — intent, projects_detected, context_length, is_new_session
    """
    # ── 1. Resolve session ────────────────────────────────────────────────────
    is_new_session = False

    if session_id:
        history = _load_conversation_history(session_id)
        if history is None:
            logger.warning("session_id %s not found in blob — creating new session", session_id)
            session_id = str(uuid.uuid4())
            history = []
            is_new_session = True
    else:
        session_id = str(uuid.uuid4())
        history = []
        is_new_session = True
        logger.info("Created new chat session %s", session_id)

    # Trim to last MAX_HISTORY_MESSAGES to keep context window manageable
    effective_history = history[-MAX_HISTORY_MESSAGES:]

    # ── 2. Detect intent ──────────────────────────────────────────────────────
    project_client = _get_project_client()
    openai_client  = project_client.get_openai_client()

    from config import MODEL_DEPLOYMENT_NAME
    deployment = MODEL_DEPLOYMENT_NAME

    intent_data    = _detect_intent(openai_client, deployment, question)
    intent         = intent_data["intent"]
    projects       = intent_data["project_names"]

    logger.info("Chat — intent: %s, projects: %s, session: %s", intent, projects, session_id)

    # ── 3. Gather context from Azure AI Search + EAC Blob Storage ────────────
    if intent == "portfolio_summary":
        context = _get_portfolio_summary()
    elif intent == "eac_query":
        context = _get_eac_context(projects) + "\n\n" + _get_project_context(question, projects, top_k=2)
    elif intent == "general":
        context = "No specific NDA project context needed for general questions."
    else:
        context = _get_project_context(question, projects, top_k=5)

    # Append EAC blob data for any query where specific projects were detected.
    # The lifecycle EAC variance file (Blob Storage) is the authoritative source
    # for EAC and schedule movements — AI Search fields are often null.
    if projects and intent != "general":
        eac_blob = _get_eac_blob_context(projects)
        if eac_blob:
            context = context + "\n\n" + eac_blob

    # ── 4. Build messages ─────────────────────────────────────────────────────
    messages = [{"role": "system", "content": _ANSWER_PROMPT}]
    messages.extend(effective_history)
    messages.append({"role": "user", "content": f"CONTEXT DATA:\n{context}\n\nUSER QUESTION:\n{question}"})

    # ── 5. Generate answer ────────────────────────────────────────────────────
    resp   = openai_client.chat.completions.create(model=deployment, messages=messages)
    answer = resp.choices[0].message.content

    # ── 6. Persist exchange ───────────────────────────────────────────────────
    updated_history = history + [
        {"role": "user",      "content": question},
        {"role": "assistant", "content": answer},
    ]
    try:
        _save_conversation_history(session_id, updated_history)
    except Exception as exc:
        logger.warning("Could not save chat session %s: %s", session_id, exc)

    return {
        "answer":     answer,
        "session_id": session_id,
        "meta": {
            "intent":            intent,
            "projects_detected": projects,
            "context_length":    len(context),
            "is_new_session":    is_new_session,
        },
    }