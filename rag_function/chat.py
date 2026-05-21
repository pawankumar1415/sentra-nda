"""
rag_function/chat.py — Conversational RAG pipeline with persistent session memory.

Answers free-form questions about the NDA portfolio using intent detection
and vector search on the indexed PostgreSQL data.

Memory behaviour
────────────────
Each chat session is identified by a `session_id` UUID.  On the first call the
server creates a new session and returns the ID to the client; the client stores
it in localStorage and sends it back on every subsequent message.  This means:

  - History survives page refreshes — the server always loads from PostgreSQL.
  - The client never needs to track or send the full message history itself.
  - Multiple browser tabs can share the same session or use independent ones.

The last MAX_HISTORY_MESSAGES messages (default 20) are injected into the LLM
prompt on each call.  Full history is retained in the DB for audit purposes.

Backward compatibility
──────────────────────
If a caller passes `history=[...]` without a `session_id` (e.g. the old frontend
or a test), the provided history is used for that call but nothing is persisted.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from db import DBConnection
from embedder import embed
from validate import _get_gpt_client, _chat_deployment
from conversation import create_session, load_history, save_turn, session_exists

_PERIOD_RE = re.compile(r'\bP\d{2}\b', re.IGNORECASE)
_RAG_COLOR_RE = re.compile(r'\b(red|amber|green)\b', re.IGNORECASE)

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

def _get_portfolio_summary(period: Optional[str] = None) -> str:
    """Fetches high-level metadata for all projects, optionally filtered by period."""
    if period:
        sql = """
            SELECT project_name, dca_rag_status, capability_capacity_rag,
                   eac_variance, schedule_variance_days, period_short_name
            FROM nda_projects
            WHERE period_short_name ILIKE %s
            ORDER BY project_name
        """
        params: tuple = (f"%{period}%",)
        label = f"PORTFOLIO DATA (Period: {period.upper()}):\n"
    else:
        sql = """
            SELECT project_name, dca_rag_status, capability_capacity_rag,
                   eac_variance, schedule_variance_days, period_short_name
            FROM nda_projects
            WHERE period_short_name = (
                SELECT period_short_name FROM nda_projects ORDER BY period_short_name DESC LIMIT 1
            )
            ORDER BY project_name
        """
        params = ()
        label = "LATEST PORTFOLIO DATA:\n"

    context = label
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return f"No portfolio data available{' for period ' + period if period else ''}."
            for r in rows:
                eac_v   = r[3] if r[3] is not None else 0.0
                sched_v = r[4] if r[4] is not None else 0
                context += (
                    f"- {r[0]} ({r[5]}): RAG={r[1]}, CapRAG={r[2]}, "
                    f"EAC Variance=£{eac_v/1_000_000:.2f}m, Schedule Slip={sched_v} days\n"
                )
    return context


def _get_status_filtered_context(rag_status: str, period: Optional[str] = None) -> str:
    """Direct query for projects matching a RAG colour, optionally filtered by period."""
    if period:
        sql = """
            SELECT project_name, period_short_name, dca_rag_status,
                   capability_capacity_rag, eac_variance, schedule_variance_days, narrative_text
            FROM nda_projects
            WHERE dca_rag_status ILIKE %s
              AND period_short_name ILIKE %s
            ORDER BY project_name
        """
        params: tuple = (f"%{rag_status}%", f"%{period}%")
        label = f"PROJECTS WITH {rag_status.upper()} RAG STATUS IN PERIOD {period.upper()}:\n"
    else:
        sql = """
            SELECT project_name, period_short_name, dca_rag_status,
                   capability_capacity_rag, eac_variance, schedule_variance_days, narrative_text
            FROM nda_projects
            WHERE dca_rag_status ILIKE %s
              AND period_short_name = (
                  SELECT period_short_name FROM nda_projects
                  ORDER BY period_short_name DESC LIMIT 1
              )
            ORDER BY project_name
        """
        params = (f"%{rag_status}%",)
        label = f"PROJECTS WITH {rag_status.upper()} RAG STATUS (LATEST PERIOD):\n"

    context = label
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return f"No projects found with {rag_status} RAG status{' in period ' + period if period else ' in the latest period'}."
            for r in rows:
                eac_v   = r[4] if r[4] is not None else 0.0
                sched_v = r[5] if r[5] is not None else 0
                narrative_snippet = (r[6] or "")[:300]
                context += (
                    f"- {r[0]} ({r[1]}): DCA RAG={r[2]}, CapRAG={r[3]}, "
                    f"EAC Variance=£{eac_v/1_000_000:.2f}m, Schedule Slip={sched_v} days\n"
                    f"  Narrative: {narrative_snippet}\n"
                )
    return context


def _get_eac_data(project_names: List[str]) -> str:
    """Fetches EAC variance explicit data (shared data)."""
    if not project_names:
        sql = "SELECT project_name, period_short_name, eac_variance, schedule_variance_days, summary_text FROM nda_eac_variance ORDER BY abs(eac_variance) DESC LIMIT 10"
        params: tuple = ()
    else:
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

def _vector_search(question: str, project_names: List[str], top_k: int = 5, periods: Optional[List[str]] = None) -> str:
    """
    pgvector cosine similarity search (shared data).
    If specific periods are detected in the question, fetches rows for each
    period explicitly so cross-period comparisons always have the right data.
    """
    enhanced_query = question
    if project_names:
        enhanced_query += " " + " ".join(project_names)

    query_vector = embed(enhanced_query)
    context = "RETRIEVED NARRATIVE CONTEXT:\n"

    with DBConnection() as conn:
        with conn.cursor() as cur:

            # If the user asked about specific periods, pull one best match per period
            # so a "compare P07 and P08" question always gets data from both
            if periods and project_names:
                for period in periods:
                    sql = """
                        SELECT project_name, period_short_name, raw_content,
                               1 - (embedding <=> %s::vector) AS score
                        FROM nda_projects
                        WHERE lower(period_short_name) = lower(%s)
                          AND lower(project_name) LIKE lower(%s)
                        ORDER BY embedding <=> %s::vector
                        LIMIT 1;
                    """
                    for pname in project_names:
                        cur.execute(sql, (query_vector, period, f"%{pname}%", query_vector))
                        row = cur.fetchone()
                        if row:
                            context += f"--- {row[0]} ({row[1]}) [Score: {row[3]:.2f}] ---\n{row[2]}\n\n"
                # If we got period-specific results, return them
                if context != "RETRIEVED NARRATIVE CONTEXT:\n":
                    return context

            # Default: top-k cosine similarity (no period filter)
            sql = """
                SELECT project_name, period_short_name, raw_content,
                       1 - (embedding <=> %s::vector) AS score
                FROM nda_projects
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
            """
            cur.execute(sql, (query_vector, query_vector, top_k))
            rows = cur.fetchall()
            if not rows:
                return "No matching narrative data found."
            for r in rows:
                context += f"--- {r[0]} ({r[1]}) [Score: {r[3]:.2f}] ---\n{r[2]}\n\n"

    return context


def run_chat(
    question: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    user_id: Optional[str] = None,
) -> Dict:
    """
    Main entry point for POST /api/chat.

    Conversation memory flow:
      1. Resolve session — create a new one or validate the supplied ID.
      2. Load history from PostgreSQL (most recent MAX_HISTORY_MESSAGES messages).
      3. Detect intent from the user's question.
      4. Gather RAG context from the database based on intent.
      5. Build the LLM message list (system + history + augmented user message).
      6. Call GPT and get the answer.
      7. Persist the user/assistant exchange back to PostgreSQL.
      8. Return answer + session_id so the client can store the ID.

    Args:
        question:   The user's question for this turn.
        session_id: UUID string of an existing session.  If None or invalid,
                    a new session is created automatically.
        history:    Legacy fallback — a list of {"role", "content"} dicts sent
                    by the client.  Used only when no valid session_id is given
                    (backward-compatible with old frontend versions).

    Returns:
        Dict with keys:
            "answer"     — markdown-formatted LLM response
            "session_id" — UUID string the client should persist (may be new)
            "meta"       — intent, projects_detected, context_length, is_new_session
    """
    # ── 1. Resolve session ────────────────────────────────────────────────────
    is_new_session = False

    if session_id and session_exists(session_id):
        # Existing session — load history from PostgreSQL
        logger.info("Resuming chat session %s", session_id)
        effective_history = load_history(session_id)
    else:
        if session_id:
            # Client sent an ID that doesn't exist — log a warning and start fresh
            # (handles stale IDs from cleared DB or re-deployments)
            logger.warning(
                "session_id %s not found in DB — creating new session", session_id
            )
        # Create a fresh session
        session_id = create_session(metadata={"source": "chat_view"}, user_id=user_id)
        is_new_session = True
        logger.info("Created new chat session %s", session_id)

        # Fall back to client-supplied history if given (legacy path)
        effective_history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in (history or [])
            if m.get("role") in ("user", "assistant")
        ]

    # ── 2. Detect intent ──────────────────────────────────────────────────────
    intent_data = _detect_intent(question)
    intent      = intent_data["intent"]
    projects    = intent_data["project_names"]

    # Extract any period references (e.g. "P07") and RAG colour mentions
    periods    = [m.upper() for m in _PERIOD_RE.findall(question)]
    rag_colors = [m.group(0) for m in _RAG_COLOR_RE.finditer(question)]

    # Also check the recent history for project names the user was just discussing
    # so follow-up questions like "compare P07 and P08" resolve correctly
    if not projects and effective_history:
        last_assistant = next(
            (m["content"] for m in reversed(effective_history) if m["role"] == "assistant"),
            ""
        )
        # Re-run intent detection on the last assistant message to recover project names
        if last_assistant:
            prior_intent = _detect_intent(last_assistant)
            projects = prior_intent.get("project_names", [])

    logger.info("Chat request — intent: %s, projects: %s, periods: %s, session: %s", intent, projects, periods, session_id)

    # ── 3. Gather RAG context from the database ───────────────────────────────
    # RAG colour filter takes priority — direct SQL beats vector search for status queries
    if rag_colors:
        context = _get_status_filtered_context(rag_colors[0], periods[0] if periods else None)
    elif intent == "portfolio_summary":
        context = _get_portfolio_summary(periods[0] if periods else None)
    elif intent == "eac_query":
        context = _get_eac_data(projects) + "\n\n" + _vector_search(question, projects, top_k=2, periods=periods)
    elif intent == "general":
        context = "No specific NDA project context needed for general questions."
    else:
        # Default: project_query — pure vector search, with period-aware fetching
        context = _vector_search(question, projects, top_k=5, periods=periods)

    # ── 4. Build LLM message list ─────────────────────────────────────────────
    # Order: system prompt → conversation history → current user question (with context)
    messages = [{"role": "system", "content": _ANSWER_PROMPT}]

    # Inject conversation history — already ordered oldest-to-newest by load_history()
    messages.extend(effective_history)

    # Augment the current question with hidden RAG context.
    # The context is hidden from the chat UI but visible to the LLM so the
    # assistant can ground its answer in real data without cluttering the display.
    augmented_user_message = f"CONTEXT DATA:\n{context}\n\nUSER QUESTION:\n{question}"
    messages.append({"role": "user", "content": augmented_user_message})

    # ── 5. Generate answer ────────────────────────────────────────────────────
    gpt  = _get_gpt_client()
    resp = gpt.chat.completions.create(
        model=_chat_deployment(),
        messages=messages,
    )
    answer = resp.choices[0].message.content

    # ── 6. Persist the exchange ───────────────────────────────────────────────
    # Save the raw user question (NOT the augmented version with context) so
    # the stored history reads naturally for humans and future LLM turns.
    save_turn(
        session_id=session_id,
        user_message=question,
        assistant_message=answer,
        metadata={
            "intent":            intent,
            "projects_detected": projects,
            "context_length":    len(context),
        },
    )

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
