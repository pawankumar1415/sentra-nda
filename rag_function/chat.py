"""
rag_function/chat.py — Conversational RAG pipeline.

Answers free-form questions about the NDA portfolio using intent detection
and vector search on the Indexed PostgreSQL data.
"""

import json
import logging
from typing import Dict, List, Any

from db import DBConnection
from embedder import embed
from validate import _get_gpt_client, _chat_deployment

logger = logging.getLogger(__name__)

# System prompt for intent detection
_INTENT_PROMPT = """You are a router. Analyze the user's question about the NDA nuclear decommissioning portfolio and determine the intent.
Output ONLY a JSON object with two keys:
1. "intent": string. Choose from:
   - "portfolio_summary" (asking about overall health, count of red/amber projects, general portfolio status)
   - "project_query" (asking about a specific project or list of projects)
   - "eac_query" (asking specifically about financial movements, EAC, or schedule delays)
   - "general" (greetings, unrelated questions)
2. "project_names": list of strings. If specific projects are mentioned (e.g., "BEPPS2", "SIXEP", "MSSS"), list them. Otherwise empty list.
"""

# System prompt for answering
_ANSWER_PROMPT = """You are the Sentra RAG Assistant, an expert AI analyzing the NDA (Nuclear Decommissioning Authority) portfolio.
You have been provided with data extracted directly from the MPPR (Major Projects Performance Report) and EAC (Estimate at Completion) variances.

Answer the user's question based strictly on the provided Context Data.
If the context data does not contain the answer, say "I don't have enough data to answer that." Do not make up financial figures or project statuses.

Formatting rules:
- Use Markdown.
- If summarizing multiple projects, use a Markdown table (e.g., | Project | Status | Detail |).
- Always mention the Reporting Period (e.g., "In P07...") if it is in the data.
- Keep answers professional, concise, and analytical.
"""

def _detect_intent(question: str) -> Dict[str, Any]:
    """Uses a fast GPT call to figure out what data to query."""
    gpt = _get_gpt_client()
    try:
        resp = gpt.chat.completions.create(
            model=_chat_deployment(),
            messages=[
                {"role": "system", "content": _INTENT_PROMPT},
                {"role": "user", "content": question}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "intent": result.get("intent", "general"),
            "project_names": result.get("project_names", [])
        }
    except Exception as e:
        logger.error(f"Intent detection failed: {e}")
        return {"intent": "project_query", "project_names": []} # Fallback to standard vector search

def _get_portfolio_summary() -> str:
    """Fetches high-level metadata for all projects in the latest period."""
    sql = """
        SELECT project_name, dca_rag_status, capability_capacity_rag, eac_variance, schedule_variance_days
        FROM nda_projects
        WHERE period_short_name = (SELECT period_short_name FROM nda_projects ORDER BY period_short_name DESC LIMIT 1)
    """
    context = "LATEST PORTFOLIO DATA:\n"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            if not rows:
                return "No portfolio data available in the database."
            for r in rows:
                eac_v = r[3] if r[3] is not None else 0.0
                sched_v = r[4] if r[4] is not None else 0
                context += f"- {r[0]}: RAG={r[1]}, CapRAG={r[2]}, EAC Variance=£{eac_v/1000000:.2f}m, Schedule Slip={sched_v} days\n"
    return context

def _get_eac_data(project_names: List[str]) -> str:
    """Fetches EAC variance explicit data."""
    if not project_names:
        sql = "SELECT project_name, period_short_name, eac_variance, schedule_variance_days, summary_text FROM nda_eac_variance ORDER BY abs(eac_variance) DESC LIMIT 10"
        params = ()
    else:
        # Just grab the first mentioned project for now, could be expanded
        sql = "SELECT project_name, period_short_name, eac_variance, schedule_variance_days, summary_text FROM nda_eac_variance WHERE lower(project_name) LIKE lower(%s) ORDER BY period_short_name DESC LIMIT 5"
        params = (f"%{project_names[0]}%",)
        
    context = "EAC & SCHEDULE VARIANCES:\n"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return "No EAC variance data found."
            for r in rows:
                context += f"- {r[0]} ({r[1]}): Variance £{r[2]/1000000:.2f}m, Slip {r[3]} days. Details: {r[4]}\n"
    return context

def _vector_search(question: str, project_names: List[str], top_k: int = 5) -> str:
    """Standard pgvector cosine similarity search."""
    query_vector = embed(question)
    
    if project_names:
        sql = """
            SELECT project_name, period_short_name, raw_content,
                   1 - (embedding <=> %s::vector) AS score
            FROM nda_projects
            WHERE lower(project_name) LIKE lower(%s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """
        params = (query_vector, f"%{project_names[0]}%", query_vector, top_k)
    else:
        sql = """
            SELECT project_name, period_short_name, raw_content,
                   1 - (embedding <=> %s::vector) AS score
            FROM nda_projects
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """
        params = (query_vector, query_vector, top_k)

    context = "RETRIEVED NARRATIVE CONTEXT:\n"
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return "No matching narrative data found."
            for r in rows:
                context += f"--- {r[0]} ({r[1]}) [Score: {r[3]:.2f}] ---\n{r[2]}\n\n"
    return context


def run_chat(question: str, history: List[Dict[str, str]]) -> Dict:
    """
    Main entry point for /api/chat.
    1. Detect intent.
    2. Gather context from DB.
    3. Generate response with GPT.
    """
    # 1. Detect Intent
    intent_data = _detect_intent(question)
    intent = intent_data["intent"]
    projects = intent_data["project_names"]
    
    logger.info(f"Chat request - Intent: {intent}, Projects: {projects}")

    # 2. Gather Context based on intent
    context = ""
    if intent == "portfolio_summary":
        context = _get_portfolio_summary()
    elif intent == "eac_query":
        context = _get_eac_data(projects) + "\n\n" + _vector_search(question, projects, top_k=2)
    elif intent == "general":
        context = "No specific NDA project context needed for general questions."
    else:
        # Default: project_query
        context = _vector_search(question, projects, top_k=5)

    # 3. Build message history (keep last 5 interactions to save tokens)
    messages = [{"role": "system", "content": _ANSWER_PROMPT}]
    
    # Add history
    for msg in history[-10:]:
        # only accept 'user' or 'assistant' roles
        role = msg.get("role", "user")
        if role in ["user", "assistant"]:
            messages.append({"role": role, "content": msg.get("content", "")})

    # Add current question + hidden context
    augmented_user_message = f"CONTEXT DATA:\n{context}\n\nUSER QUESTION:\n{question}"
    messages.append({"role": "user", "content": augmented_user_message})

    # 4. Generate Answer
    gpt = _get_gpt_client()
    resp = gpt.chat.completions.create(
        model=_chat_deployment(),
        messages=messages
    )

    answer = resp.choices[0].message.content

    return {
        "answer": answer,
        "meta": {
            "intent": intent,
            "projects_detected": projects,
            "context_length": len(context)
        }
    }
