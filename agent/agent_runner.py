"""
agent/agent_runner.py — Blob-Storage Conversation Memory + Azure AI Foundry Agent.

Conversation memory is implemented at two complementary levels:

  SHORT-TERM (within a session) — Azure Blob Storage
  ────────────────────────────────────────────────────
  Each call to validate_narrative() can receive a `conversation_id` (a UUID we
  generate) from the client.  On the first call (no ID supplied) a fresh UUID is
  created and the conversation history blob is initialised.  On follow-up calls
  the client returns the same UUID; the server loads the message history from
  blob storage and injects it into the Responses API `input` so the agent has
  full context of prior turns — no Azure Conversations API permissions required.

  LONG-TERM (across sessions) — Azure AI Foundry Memory Store (Preview)
  ────────────────────────────────────────────────────────────────────────
  The agent has a MemorySearchPreviewTool attached (via setup_memory.py or the
  Foundry portal → Memory → Add).  After each session Azure extracts key facts
  (projects validated, recurring issues, user preferences) and stores them in
  the Memory Store.  On every subsequent call those memories are automatically
  retrieved and injected into the agent context via the `agent_reference`
  extra_body field.  The `user_scope` parameter scopes memory to a specific
  user/tenant so different users don't share memories.

Architecture
────────────
Primary path  : Responses API (history injected via `input` list)
Fallback path : Chat Completions (history injected via messages list)

Conversation blobs are stored in Azure Blob Storage under:
    <AZURE_STORAGE_CONTAINER_NAME>/conversations/<uuid>.json
with the schema:
    {"messages": [{"role": "user"|"assistant", "content": "..."}]}
"""

from __future__ import annotations

import json
import logging
import os
import uuid
import inspect
from typing import Callable, List, Optional, Tuple

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.storage.blob import BlobServiceClient

import azure.ai.projects.models as models

from config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
    MEMORY_STORE_NAME,
    MEMORY_UPDATE_DELAY_SECONDS,
    AZURE_STORAGE_ACCOUNT_URL,
    AZURE_STORAGE_CONTAINER_NAME,
)
from system_prompt import get_system_prompt
from tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

AGENT_NAME = "nda-narrative-validator-v3"

# Blob prefix used for all conversation history files.
# Stored alongside EAC data in the same container but under a separate prefix.
CONVERSATION_BLOB_PREFIX = "conversations/"


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


def _get_blob_service_client() -> BlobServiceClient:
    """Build a BlobServiceClient using the same credential chain as the AI client."""
    credential = DefaultAzureCredential()
    tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
    client_id     = os.environ.get("AZURE_CLIENT_ID", "")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
    if tenant_id and client_id and client_secret:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    return BlobServiceClient(
        account_url=AZURE_STORAGE_ACCOUNT_URL,
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
# Conversation History Management (Azure Blob Storage)
# ─────────────────────────────────────────────────────────────────────────────

def _load_conversation_history(conv_id: str) -> Optional[List[dict]]:
    """
    Load conversation history from Azure Blob Storage.

    Returns a list of {"role", "content"} dicts in chronological order,
    or None if the blob does not exist or cannot be read.
    """
    try:
        blob_client = _get_blob_service_client()
        blob = blob_client.get_blob_client(
            container=AZURE_STORAGE_CONTAINER_NAME,
            blob=f"{CONVERSATION_BLOB_PREFIX}{conv_id}.json",
        )
        raw = blob.download_blob().readall()
        data = json.loads(raw)
        return data.get("messages", [])
    except Exception as exc:
        logger.debug("Could not load conversation %s from blob: %s", conv_id, exc)
        return None


def _save_conversation_history(conv_id: str, messages: List[dict]) -> None:
    """
    Save/overwrite conversation history in Azure Blob Storage.

    Non-fatal: if the write fails the validation result is still returned;
    subsequent calls simply cannot resume the conversation.
    """
    try:
        blob_client = _get_blob_service_client()
        blob = blob_client.get_blob_client(
            container=AZURE_STORAGE_CONTAINER_NAME,
            blob=f"{CONVERSATION_BLOB_PREFIX}{conv_id}.json",
        )
        blob.upload_blob(
            json.dumps({"messages": messages}, ensure_ascii=False),
            overwrite=True,
        )
        logger.info(
            "Saved conversation %s (%d messages) to blob", conv_id, len(messages)
        )
    except Exception as exc:
        logger.warning("Could not save conversation %s to blob: %s", conv_id, exc)


def _resolve_conversation(
    conv_id: Optional[str],
    user_prompt: str,
) -> Tuple[str, bool, List[dict]]:
    """
    Load or create a conversation session using Azure Blob Storage.

    If `conv_id` is provided and the corresponding blob exists, the prior history
    is returned.  If the blob is missing (expired / random ID) a new UUID is
    created instead.

    Returns:
        (conversation_id, is_new, prior_history)
        prior_history — list of {"role", "content"} dicts for all turns BEFORE
        the current user message.  The caller appends the current user prompt.
    """
    if conv_id:
        history = _load_conversation_history(conv_id)
        if history is not None:
            logger.info(
                "Resumed conversation %s (%d prior messages)", conv_id, len(history)
            )
            return conv_id, False, history
        logger.warning(
            "Conversation %s not found in blob storage — starting new session", conv_id
        )

    new_id = str(uuid.uuid4())
    logger.info("Created new conversation %s", new_id)
    return new_id, True, []


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

    SHORT-TERM MEMORY (Blob Storage):
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
        conversation_id: UUID from a previous response.  Pass None to start new.
        user_scope:      String used to partition the Memory Store per user.

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

    logger.info(
        "Starting validation for: %s (conversation_id=%s)", project_name, conversation_id
    )

    # ── Resolve / create short-term conversation session (Blob Storage) ────────
    conversation_id, is_new, prior_history = _resolve_conversation(
        conversation_id, user_prompt
    )

    # ── Run validation (primary: Responses API; fallback: Chat Completions) ──
    try:
        result_text = _run_with_responses_api(
            openai_client, user_prompt, prior_history, user_scope
        )
    except Exception as e:
        logger.warning(
            "Responses API failed (%s) — falling back to Chat Completions", e
        )
        result_text = _run_with_chat_completions_with_history(
            openai_client, user_prompt, prior_history
        )

    # ── Persist the updated conversation history ───────────────────────────────
    updated_history = prior_history + [
        {"role": "user",      "content": user_prompt},
        {"role": "assistant", "content": result_text},
    ]
    try:
        _save_conversation_history(conversation_id, updated_history)
    except Exception as exc:
        # Non-fatal — validation already completed; log and continue
        logger.warning("Conversation history save failed: %s", exc)

    return {
        "conversation_id":     conversation_id,
        "is_new_conversation": is_new,
        "validation_result":   result_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Responses API Path (Primary)
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_responses_api(
    openai_client,
    user_prompt: str,
    prior_history: List[dict],
    user_scope: Optional[str],
) -> str:
    """
    Primary execution path using the OpenAI Responses API.

    Injects `prior_history` as earlier input items so the agent has full context
    of the conversation without relying on the Azure Conversations API (which
    requires elevated Foundry project permissions).

    The agent has a MemorySearchPreviewTool attached in the Foundry portal;
    long-term memories are automatically injected via `agent_reference`.
    `user_scope` scopes the memory retrieval to a specific user.
    """
    openai_tools = _build_responses_api_tools()

    # Build the extra_body to wire up the agent (and its attached Memory Store)
    extra_body: dict = {
        "agent_reference": {
            "name": AGENT_NAME,
            "type": "agent_reference",
        },
    }
    if user_scope:
        extra_body["memory_scope"] = user_scope

    # Build `input` as prior history + current user message.
    # Each item follows the Responses API input item format.
    input_items: list = [
        {"type": "message", "role": msg["role"], "content": msg["content"]}
        for msg in prior_history
    ]
    input_items.append({"type": "message", "role": "user", "content": user_prompt})

    response = openai_client.responses.create(
        model=MODEL_DEPLOYMENT_NAME,
        instructions=get_system_prompt(),
        tools=openai_tools,
        input=input_items,
        extra_body=extra_body,
    )

    # ── Tool-call loop ────────────────────────────────────────────────────────
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
    prior_history: List[dict],
) -> str:
    """
    Fallback execution path using the Chat Completions API.

    Builds a messages list from prior_history so this path also has short-term
    memory.  Long-term Memory Store injection is NOT available in this path —
    it requires the Responses API + agent_reference.

    Args:
        openai_client:  OpenAI client from get_openai_client().
        user_prompt:    The current user message.
        prior_history:  List of {"role", "content"} dicts for previous turns.
    """
    openai_tools = _build_chat_completions_tools()

    # ── Build messages list ───────────────────────────────────────────────────
    messages: list[dict] = [{"role": "system", "content": get_system_prompt()}]
    messages.extend(prior_history)
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