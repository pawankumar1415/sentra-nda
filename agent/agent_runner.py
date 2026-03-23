"""
agent/agent_runner.py — SDK 2.0.0 Compatible Version with Conversation Memory.

Conversation memory is implemented at two complementary levels:

  SHORT-TERM (within a session) — Azure Conversations API
  ────────────────────────────────────────────────────────
  Each call to validate_narrative() can receive a `conversation_id` from the
  client.  On the first call (no ID supplied) a new Conversations object is
  created via openai_client.conversations.create(), and its ID is returned to
  the client.  On follow-up calls the client passes the same ID; the server
  adds the new user message to the existing conversation and calls the Responses
  API with `conversation=conversation_id` — Azure reconstructs the full history
  automatically without re-sending every message.

  LONG-TERM (across sessions) — Azure AI Foundry Memory Store (Preview)
  ────────────────────────────────────────────────────────────────────────
  The agent has a MemorySearchPreviewTool attached (via setup_memory.py or the
  Foundry portal → Memory → Add).  After each session Azure extracts key facts
  (projects validated, recurring issues, user preferences) and stores them in
  the Memory Store.  On every subsequent call those memories are automatically
  retrieved and injected into the agent context — the agent "remembers" past
  interactions without being explicitly told.

  The `user_scope` parameter scopes memory to a specific user/tenant so
  different users don't share memories.

Architecture
────────────
Primary path  : Responses API + Conversations (requires Azure AI Developer role)
Fallback path : Chat Completions (stateless; used if Responses API fails)

When the fallback path runs, short-term context is reconstructed by loading
conversation items from the Conversations object and converting them to a
messages list — so even the fallback path has memory, just managed slightly
differently.

SDK version required: azure-ai-projects >= 2.0.0 (stable, released March 2026)
"""

from __future__ import annotations

import json
import logging
import os
import inspect
from typing import Callable, List, Optional, Tuple

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential

import azure.ai.projects.models as models

from config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
    MEMORY_STORE_NAME,
    MEMORY_UPDATE_DELAY_SECONDS,
)
from system_prompt import get_system_prompt
from tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

AGENT_NAME = "nda-narrative-validator-v3"


# ─────────────────────────────────────────────────────────────────────────────
# Client Setup
# ─────────────────────────────────────────────────────────────────────────────

def _get_project_client() -> AIProjectClient:
    """
    Build an AIProjectClient using either a Service Principal (when all three
    AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET env vars are set)
    or DefaultAzureCredential (az login locally, Managed Identity on Azure).
    """
    tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
    client_id     = os.environ.get("AZURE_CLIENT_ID", "")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")

    if tenant_id and client_id and client_secret:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    else:
        credential = DefaultAzureCredential()

    return AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Function Schema Generator
# ─────────────────────────────────────────────────────────────────────────────

def _get_function_schema(func: Callable) -> dict:
    """Generate a JSON Schema dict from a Python function's type annotations."""
    spec      = inspect.getfullargspec(func)
    params    = {"type": "object", "properties": {}, "required": []}
    type_map  = {str: "string", int: "integer", float: "number", bool: "boolean",
                 dict: "object", list: "array"}
    defaults  = spec.defaults or []
    default_map = dict(zip(spec.args[-len(defaults):], defaults)) if defaults else {}

    for arg in spec.args:
        if arg == "self":
            continue
        json_type = type_map.get(spec.annotations.get(arg, str), "string")
        params["properties"][arg] = {"type": json_type}
        if arg not in default_map:
            params["required"].append(arg)
    return params


def _build_responses_api_tools() -> list[dict]:
    """
    Build tool definitions for the OpenAI Responses API.
    In the Responses API, name/description/parameters are top-level fields
    (not nested under a 'function' key as in Chat Completions).
    """
    tools = []
    for func in AGENT_TOOLS:
        schema = _get_function_schema(func)
        tools.append({
            "type":        "function",
            "name":        func.__name__,
            "description": (func.__doc__ or "").strip(),
            "parameters":  schema,
        })
    return tools


def _build_chat_completions_tools() -> list[dict]:
    """
    Build tool definitions for the Chat Completions API.
    In Chat Completions, name/description/parameters are nested under 'function'.
    """
    tools = []
    for func in AGENT_TOOLS:
        schema = _get_function_schema(func)
        tools.append({
            "type": "function",
            "function": {
                "name":        func.__name__,
                "description": (func.__doc__ or "").strip(),
                "parameters":  schema,
            },
        })
    return tools


# ─────────────────────────────────────────────────────────────────────────────
# Tool Dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_tool(fn_name: str, fn_args: dict) -> str:
    """Look up and call a tool function by name; return its result as a JSON string."""
    import tools as tool_module
    fn = getattr(tool_module, fn_name, None)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {fn_name}"})
    try:
        return fn(**fn_args)
    except Exception as exc:
        logger.exception("Tool %s raised an error", fn_name)
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Conversation Management (short-term session memory)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_conversation(
    openai_client,
    user_prompt: str,
    conversation_id: Optional[str],
) -> Tuple[str, bool]:
    """
    Get or create a Conversations API session and add the user's message to it.

    If `conversation_id` is provided the user message is appended to the
    existing conversation.  Otherwise a new Conversations object is created
    with the user message as the first item.

    Returns:
        (conversation_id, is_new) — the UUID string and a bool indicating
        whether a new conversation was created.
    """
    user_item = {
        "type":    "message",
        "role":    "user",
        "content": user_prompt,
    }

    if conversation_id:
        # Resume existing conversation — append user turn
        try:
            openai_client.conversations.items.create(
                conversation_id=conversation_id,
                items=[user_item],
            )
            logger.info("Resumed conversation %s", conversation_id)
            return conversation_id, False
        except Exception as exc:
            # Conversation may have expired or been deleted — start fresh
            logger.warning(
                "Could not resume conversation %s (%s) — creating new one",
                conversation_id, exc,
            )

    # Create a new conversation with the user message as the seed item
    conv            = openai_client.conversations.create(items=[user_item])
    conversation_id = conv.id
    logger.info("Created new conversation %s", conversation_id)
    return conversation_id, True


def _store_assistant_reply(
    openai_client,
    conversation_id: str,
    reply_text: str,
) -> None:
    """
    Write the assistant's reply back into the Conversations object so future
    turns have access to it.

    This is necessary because the Responses API does not automatically write
    the assistant turn back when we use the tool-call loop (we pass
    previous_response_id, not conversation=, during the tool loop to avoid
    double-writing intermediate steps).
    """
    try:
        openai_client.conversations.items.create(
            conversation_id=conversation_id,
            items=[{
                "type":    "message",
                "role":    "assistant",
                "content": reply_text,
            }],
        )
    except Exception as exc:
        # Non-fatal — the reply is already returned to the user; this is just
        # for future context retrieval.
        logger.warning("Could not store assistant reply in conversation: %s", exc)


def _conversation_to_messages(openai_client, conversation_id: str) -> List[dict]:
    """
    Load conversation items and convert them to a Chat Completions messages list.

    Used by the fallback Chat Completions path so it has the same conversation
    history as the Responses API primary path.

    Returns a list of {"role": ..., "content": ...} dicts in chronological order,
    filtered to user/assistant roles only (tool calls are not passed to Chat
    Completions as history — they are re-executed fresh if needed).
    """
    try:
        items = list(openai_client.conversations.items.list(conversation_id))
    except Exception as exc:
        logger.warning("Could not load conversation items for %s: %s", conversation_id, exc)
        return []

    messages = []
    for item in items:
        role    = getattr(item, "role", None)
        content = getattr(item, "content", None)
        if role in ("user", "assistant") and content:
            # content may be a string or a list of content blocks
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Extract text from content block list
                text = " ".join(
                    getattr(block, "text", "") or ""
                    for block in content
                    if getattr(block, "type", "") in ("text", "output_text")
                )
                if text:
                    messages.append({"role": role, "content": text})
    return messages


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def validate_narrative(
    project_name: str,
    narrative_text: str,
    period: str,
    conversation_id: Optional[str] = None,
    user_scope: Optional[str] = None,
) -> dict:
    """
    Validate a project narrative using the Azure AI Foundry agent with
    two-level conversation memory.

    SHORT-TERM MEMORY (Conversations API):
        Pass `conversation_id` from a previous response to continue a session.
        The agent will remember the original narrative, validation result, and
        any follow-up exchanges within the same conversation.

    LONG-TERM MEMORY (Memory Store):
        Pass `user_scope` (e.g. a user ID or tenant ID) to scope memories to
        a specific user.  The Memory Store automatically extracts and recalls
        facts across sessions — no extra code needed in the caller.

    Args:
        project_name:    NDA project name used for EAC lookup and context.
        narrative_text:  The narrative text to validate (or a follow-up question
                         in a continuing conversation).
        period:          Reporting period string, e.g. "P07 2025-26".
        conversation_id: Azure Conversations API UUID from a previous response.
                         Pass None to start a new conversation session.
        user_scope:      String used to partition the Memory Store per user.
                         If None the default shared scope is used — not
                         recommended in multi-user production deployments.

    Returns:
        Dict with keys:
            "conversation_id"   — UUID string; store client-side for follow-ups
            "is_new_conversation" — True if a new session was created
            "validation_result" — the agent's validation text / JSON
    """
    client        = _get_project_client()
    openai_client = client.get_openai_client()

    # Build the structured user prompt for this turn
    user_prompt = (
        f"Please validate the narrative for **{project_name}** ({period}):\n\n"
        f"{narrative_text}"
    )

    logger.info("Starting validation for: %s (conversation_id=%s)", project_name, conversation_id)

    # ── Resolve / create short-term conversation session ──────────────────────
    conversation_api_error: str | None = None
    try:
        conversation_id, is_new = _resolve_conversation(
            openai_client, user_prompt, conversation_id
        )
    except Exception as exc:
        # Conversations API may be unavailable (permissions / region) — fall
        # back to a plain stateless call and log the failure.
        conversation_api_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Conversations API unavailable (%s) — running stateless validation",
            conversation_api_error,
        )
        conversation_id = None
        is_new          = True

    # ── Run validation (primary: Responses API; fallback: Chat Completions) ──
    try:
        result_text = _run_with_responses_api(
            openai_client, user_prompt, conversation_id, user_scope
        )
    except Exception as e:
        logger.warning(
            "Responses API failed (%s) — falling back to Chat Completions", e
        )
        result_text = _run_with_chat_completions_with_history(
            openai_client, user_prompt, conversation_id
        )

    # ── Store the assistant reply back in the Conversations object ────────────
    if conversation_id:
        _store_assistant_reply(openai_client, conversation_id, result_text)

    response: dict = {
        "conversation_id":     conversation_id or "stateless",
        "is_new_conversation": is_new,
        "validation_result":   result_text,
    }
    # Surface the conversation API error when falling back to stateless so
    # callers can diagnose permission / SDK issues without needing log access.
    if conversation_api_error:
        response["conversation_api_error"] = conversation_api_error
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Responses API Path (Primary)
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_responses_api(
    openai_client,
    user_prompt: str,
    conversation_id: Optional[str],
    user_scope: Optional[str],
) -> str:
    """
    Primary execution path using the OpenAI Responses API.

    Uses `conversation=conversation_id` so Azure automatically reconstructs
    the full conversation history from the Conversations object — we do not
    resend old messages.

    The agent has a MemorySearchPreviewTool attached in the Foundry portal;
    long-term memories are automatically injected into the agent context via
    the `agent_reference` extra_body field.  `user_scope` scopes the memory
    retrieval to a specific user.
    """
    openai_tools = _build_responses_api_tools()

    # Build the extra_body to wire up the agent (and its attached Memory Store)
    extra_body: dict = {
        "agent_reference": {
            "name": AGENT_NAME,
            "type": "agent_reference",
        },
    }
    # If a user scope is provided, pass it so the Memory Store retrieves only
    # memories for this user rather than the shared default scope.
    if user_scope:
        extra_body["memory_scope"] = user_scope

    # Build the initial Responses API call.
    # If we have a conversation_id we use `conversation=` (history is on Azure's side).
    # Otherwise fall back to `input=` (stateless single call).
    create_kwargs: dict = {
        "model":       MODEL_DEPLOYMENT_NAME,
        "instructions": get_system_prompt(),
        "tools":       openai_tools,
        "extra_body":  extra_body,
    }
    if conversation_id:
        # The Conversations object already contains the user message we appended
        # in _resolve_conversation; just reference the conversation and Azure will
        # pick up all items including the latest user turn.
        create_kwargs["conversation"] = conversation_id
    else:
        # No conversation context — pass the user prompt directly
        create_kwargs["input"] = user_prompt

    response = openai_client.responses.create(**create_kwargs)

    # ── Tool-call loop ────────────────────────────────────────────────────────
    # The Responses API may ask us to execute tool functions (check_eac_variance,
    # etc.) and provide their results before generating the final answer.
    max_iterations = 10
    for iteration in range(max_iterations):
        tool_calls = [
            item for item in response.output
            if getattr(item, "type", "") == "function_call"
        ]

        if not tool_calls:
            break   # No pending tool calls — agent has its final answer

        # Execute each requested tool and collect outputs
        tool_outputs = []
        for tc in tool_calls:
            fn_name = tc.name
            fn_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            logger.info("Agent calling tool: %s(%s)", fn_name, list(fn_args.keys()))
            output = _dispatch_tool(fn_name, fn_args)
            tool_outputs.append({
                "type":    "function_call_output",
                "call_id": tc.call_id,
                "output":  output,
            })

        # Continue the Responses API loop with tool outputs.
        # Use previous_response_id (not conversation=) for the tool-return turns
        # to avoid double-writing intermediate assistant turns into the
        # Conversations object — we write only the final answer in validate_narrative().
        response = openai_client.responses.create(
            model=MODEL_DEPLOYMENT_NAME,
            instructions=get_system_prompt(),
            tools=openai_tools,
            input=tool_outputs,
            previous_response_id=response.id,
            extra_body=extra_body,
        )

    # ── Extract text from the final response ─────────────────────────────────
    text_parts = []
    for item in response.output:
        if getattr(item, "type", "") == "message":
            for content_block in getattr(item, "content", []):
                if getattr(content_block, "type", "") == "output_text":
                    text_parts.append(content_block.text)

    return "\n".join(text_parts) if text_parts else "(No response from agent)"


# ─────────────────────────────────────────────────────────────────────────────
# Chat Completions Fallback Path
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_chat_completions_with_history(
    openai_client,
    user_prompt: str,
    conversation_id: Optional[str],
) -> str:
    """
    Fallback execution path using the Chat Completions API.

    Reconstructs conversation history from the Conversations object so this
    path also has short-term memory even without the Responses API.
    Long-term Memory Store injection is NOT available in this path — it requires
    the Responses API + agent_reference.

    Args:
        openai_client:   OpenAI client from get_openai_client().
        user_prompt:     The current user message.
        conversation_id: Optional Conversations API UUID to load history from.
    """
    openai_tools = _build_chat_completions_tools()

    # ── Build messages list ───────────────────────────────────────────────────
    messages: list[dict] = [{"role": "system", "content": get_system_prompt()}]

    # If we have a conversation ID, load prior turns from the Conversations object
    # so the fallback path benefits from the same short-term memory.
    if conversation_id:
        prior_messages = _conversation_to_messages(openai_client, conversation_id)
        # prior_messages already includes the user turn we just appended,
        # so we include all but the last item (we'll add the current user message below)
        if prior_messages and prior_messages[-1].get("role") == "user":
            messages.extend(prior_messages[:-1])  # exclude the duplicate user turn
        else:
            messages.extend(prior_messages)

    # Add the current user question
    messages.append({"role": "user", "content": user_prompt})

    # ── Tool-call loop ────────────────────────────────────────────────────────
    max_iterations = 10
    for _ in range(max_iterations):
        completion = openai_client.chat.completions.create(
            model=MODEL_DEPLOYMENT_NAME,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
        )

        choice = completion.choices[0]

        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            # Agent wants to call tools — dispatch them and feed results back
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                logger.info("Agent calling tool: %s(%s)", fn_name, list(fn_args.keys()))
                output = _dispatch_tool(fn_name, fn_args)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      output,
                })
            continue

        # Agent is done — return its final message
        return choice.message.content or "(No response from agent)"

    return "(Agent exceeded maximum tool call iterations)"
