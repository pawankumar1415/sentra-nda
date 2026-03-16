"""
agent/agent_runner.py — SDK 2.0.0b4 Compatible Version

SDK 2.0.0b4 has fundamentally changed its architecture:
  - client.agents = Agent CRUD only (create, list, get, delete, versions)
  - client.get_openai_client() = Returns an OpenAI client for actual interaction
  - NO threads, runs, or messages in the Azure SDK anymore

This version:
1. Uses PromptAgentDefinition + _create_agent for agent registration.
2. Uses the OpenAI Responses API for actual agent interaction.
3. Handles function tool calls via the Responses API loop.
"""

from __future__ import annotations

import json
import logging
import os
import inspect
from typing import Optional, Any, Callable

# Standard project client
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential

# Flexible imports for models
import azure.ai.projects.models as models

from config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
)
from system_prompt import get_system_prompt
from tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

AGENT_NAME = "nda-narrative-validator-v3"


# ─────────────────────────────────────────────────────────────────────────────
# Client Setup
# ─────────────────────────────────────────────────────────────────────────────

def _get_project_client() -> AIProjectClient:
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
    """Manually generates a JSON Schema from a Python function's signature."""
    spec = inspect.getfullargspec(func)
    params = {"type": "object", "properties": {}, "required": []}
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}
    defaults = spec.defaults or []
    default_map = dict(zip(spec.args[-len(defaults):], defaults)) if defaults else {}

    for arg in spec.args:
        if arg == 'self': continue
        json_type = type_map.get(spec.annotations.get(arg, str), "string")
        params["properties"][arg] = {"type": json_type}
        if arg not in default_map:
            params["required"].append(arg)
    return params


def _build_responses_api_tools() -> list[dict]:
    """
    Builds tool definitions for the OpenAI Responses API.
    Responses API format: name/description/parameters are TOP-LEVEL, not nested.
    """
    tools = []
    for func in AGENT_TOOLS:
        schema = _get_function_schema(func)
        tools.append({
            "type": "function",
            "name": func.__name__,
            "description": (func.__doc__ or "").strip(),
            "parameters": schema,
        })
    return tools


def _build_chat_completions_tools() -> list[dict]:
    """
    Builds tool definitions for the Chat Completions API.
    Chat Completions format: name/description/parameters are NESTED under 'function'.
    """
    tools = []
    for func in AGENT_TOOLS:
        schema = _get_function_schema(func)
        tools.append({
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": (func.__doc__ or "").strip(),
                "parameters": schema,
            }
        })
    return tools


# ─────────────────────────────────────────────────────────────────────────────
# Tool Dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_tool(fn_name: str, fn_args: dict) -> str:
    import tools as tool_module
    fn = getattr(tool_module, fn_name, None)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {fn_name}"})
    try:
        return fn(**fn_args)
    except Exception as exc:
        logger.exception("Tool %s error", fn_name)
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def validate_narrative(
    project_name: str,
    narrative_text: str,
    period: str,
    thread_id: Optional[str] = None,
) -> dict:
    """
    Validates a project narrative using the OpenAI Responses API.

    SDK 2.0.0b4 architecture:
      1. Get an OpenAI client via AIProjectClient.get_openai_client()
      2. Call openai_client.responses.create() with model, instructions, tools, input
      3. Handle function_call outputs in a loop until done
    """
    client = _get_project_client()

    # Get the OpenAI client from the Azure AI Projects client
    openai_client = client.get_openai_client()

    # Build the user prompt
    user_prompt = (
        f"Please validate the narrative for **{project_name}** ({period}):\n\n"
        f"{narrative_text}"
    )

    logger.info("Sending validation request for: %s", project_name)

    # --- Try Responses API first, fall back to Chat Completions ---
    try:
        result_text = _run_with_responses_api(openai_client, user_prompt)
    except (AttributeError, TypeError) as e:
        logger.warning("Responses API not available (%s), falling back to Chat Completions", e)
        result_text = _run_with_chat_completions(openai_client, user_prompt)

    return {"thread_id": thread_id or "responses-api", "validation_result": result_text}


def _run_with_responses_api(openai_client, user_prompt: str) -> str:
    """
    Uses the new OpenAI Responses API (openai >= 1.66).
    Handles tool calls in a loop.
    """
    openai_tools = _build_responses_api_tools()
    response = openai_client.responses.create(
        model=MODEL_DEPLOYMENT_NAME,
        instructions=get_system_prompt(),
        tools=openai_tools,
        input=user_prompt,
    )

    # The responses API may return tool calls that need handling
    max_iterations = 10
    for _ in range(max_iterations):
        # Check if there are any function calls in the output
        tool_calls = [item for item in response.output if getattr(item, 'type', '') == 'function_call']

        if not tool_calls:
            # No more tool calls — extract the text response
            break

        # Process tool calls and build input for next round
        tool_outputs = []
        for tc in tool_calls:
            fn_name = tc.name
            fn_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            logger.info("Agent calling tool: %s(%s)", fn_name, list(fn_args.keys()))
            output = _dispatch_tool(fn_name, fn_args)
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": tc.call_id,
                "output": output,
            })

        # Continue the conversation with tool outputs
        response = openai_client.responses.create(
            model=MODEL_DEPLOYMENT_NAME,
            instructions=get_system_prompt(),
            tools=openai_tools,
            input=tool_outputs,
            previous_response_id=response.id,
        )

    # Extract text from the final response
    text_parts = []
    for item in response.output:
        if getattr(item, 'type', '') == 'message':
            for content_block in getattr(item, 'content', []):
                if getattr(content_block, 'type', '') == 'output_text':
                    text_parts.append(content_block.text)
    
    return "\n".join(text_parts) if text_parts else "(No response from agent)"


def _run_with_chat_completions(openai_client, user_prompt: str) -> str:
    """
    Fallback: Uses the standard Chat Completions API with tool calling.
    """
    openai_tools = _build_chat_completions_tools()
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": user_prompt},
    ]

    max_iterations = 10
    for _ in range(max_iterations):
        completion = openai_client.chat.completions.create(
            model=MODEL_DEPLOYMENT_NAME,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
        )

        choice = completion.choices[0]

        # If the model wants to call tools
        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                logger.info("Agent calling tool: %s(%s)", fn_name, list(fn_args.keys()))
                output = _dispatch_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
            continue

        # Model is done — return the text
        return choice.message.content or "(No response from agent)"

    return "(Agent exceeded maximum tool call iterations)"
