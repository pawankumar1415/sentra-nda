"""
agent/agent_runner.py — Ultra-Resilient Next Gen SDK Version

This version correctly handles:
1. Deeply nested AzureAISearchTool structure (v2.0.0b4+)
2. Manual FunctionTool mapping to bypass broken High-Level SDK helpers.
3. PromptAgentDefinition for standard instruction-based agents in v2.x Beta.
4. Multiple fallbacks for agent listing, creation, and status checking.
"""

from __future__ import annotations

import json
import logging
import os
import time
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

# Agent Name v3
AGENT_NAME = "nda-narrative-validator-v3"


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


def _get_function_schema(func: Callable) -> dict:
    """
    Manually generates a JSON Schema for a Python function.
    Bypasses the broken SDK helper for FunctionTool(functions=[...]).
    """
    spec = inspect.getfullargspec(func)
    params = {
        "type": "object",
        "properties": {},
        "required": []
    }
    
    # Simple type mapping
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array"
    }

    # Annotations are in spec.annotations
    # Defaults are in spec.defaults (trailing arguments)
    defaults = spec.defaults or []
    default_map = dict(zip(spec.args[-len(defaults):], defaults)) if defaults else {}

    for arg in spec.args:
        if arg == 'self': continue
        
        arg_type = spec.annotations.get(arg, str)
        # Handle Optional/Union if needed
        json_type = type_map.get(arg_type, "string")
        
        params["properties"][arg] = {"type": json_type}
        if arg not in default_map:
            params["required"].append(arg)

    return params


def _get_or_create_agent(client: AIProjectClient):
    """
    Tries multiple methods to find existing agents, then creates one if not found.
    Handles the 'list_agents' vs 'list' vs 'get_agents' confusion in Beta SDKs.
    """
    agents_list = []
    
    # Try all possible names for listing agents in different 2.x previews
    list_methods = ["list_agents", "list", "get_agents"]
    found_method = False
    
    for method_name in list_methods:
        method = getattr(client.agents, method_name, None)
        if method:
            try:
                agents_list = list(method())
                logger.info("Found existing agents using: client.agents.%s()", method_name)
                found_method = True
                break
            except Exception as e:
                logger.debug("Failed to call client.agents.%s(): %s", method_name, e)
    
    if not found_method:
        logger.warning("Could not find a method to list agents. Creating one blindly.")

    for agent in agents_list:
        name_val = getattr(agent, "name", None) or getattr(agent, "display_name", None)
        if name_val == AGENT_NAME:
            logger.info("Reusing existing agent: %s (%s)", name_val, agent.id)
            return agent

    logger.info("Creating new agent: %s", AGENT_NAME)
    use_ai_search = os.environ.get("USE_AI_SEARCH", "false").lower() == "true"
    
    tools_list = []
    
    # 1. Search Tool - HANDLING NESTED STRUCTURE (v2.0.0b4+)
    if use_ai_search:
        SearchToolClass     = getattr(models, "AzureAISearchTool", None)
        ToolResourceClass   = getattr(models, "AzureAISearchToolResource", None)
        IndexResourceClass  = getattr(models, "AISearchIndexResource", None)
        
        if SearchToolClass and ToolResourceClass and IndexResourceClass:
            try:
                index_resource = IndexResourceClass(
                    project_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                    index_name=AZURE_SEARCH_INDEX_NAME
                )
                tool_resource = ToolResourceClass(indexes=[index_resource])
                tools_list.append(SearchToolClass(azure_ai_search=tool_resource))
                logger.info("Search tool added (nested structure)")
            except Exception:
                try:
                    tools_list.append(SearchToolClass(
                        index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                        index_name=AZURE_SEARCH_INDEX_NAME
                    ))
                except Exception: pass

    # 2. Function Tools - MANUAL MAPPING (v2.0.0b4+)
    FunctionToolClass = getattr(models, "FunctionTool", None)
    if FunctionToolClass:
        for func in AGENT_TOOLS:
            try:
                schema = _get_function_schema(func)
                tool = FunctionToolClass(
                    name=func.__name__,
                    description=func.__doc__ or "No description provided.",
                    parameters=schema,
                    strict=True
                )
                tools_list.append(tool)
                logger.info("Function tool added: %s", func.__name__)
            except Exception as e:
                logger.error("Failed to add function tool %s: %s", func.__name__, e)

    # 3. Agent Creation - HANDLING PromptAgentDefinition (v2.0.0b4+)
    create_method = getattr(client.agents, "create_agent", None) or getattr(client.agents, "_create_agent", None)
    if not create_method:
        raise RuntimeError("Could not find a method to create agents")

    try:
        # High-level attempt (OpenAI-like)
        agent = create_method(
            model=MODEL_DEPLOYMENT_NAME,
            name=AGENT_NAME,
            instructions=get_system_prompt(),
            tools=tools_list
        )
    except TypeError:
        # Low-level attempt (requires PromptAgentDefinition)
        logger.info("Falling back to PromptAgentDefinition for creation...")
        PromptDefClass = getattr(models, "PromptAgentDefinition", None)
        if not PromptDefClass:
            raise RuntimeError("PromptAgentDefinition class missing in models")
            
        definition = PromptDefClass(
            model=MODEL_DEPLOYMENT_NAME,
            instructions=get_system_prompt(),
            tools=tools_list
        )
        # Check signature again
        sig = inspect.signature(create_method)
        if "definition" in sig.parameters:
            agent = create_method(name=AGENT_NAME, definition=definition)
        else:
            # Maybe it just takes definition?
            agent = create_method(definition)

    logger.info("Agent created: %s", agent.id)
    return agent


def _run_and_wait(client: AIProjectClient, agent_id: str, thread_id: str, user_message: str) -> str:
    client.agents.create_message(
        thread_id=thread_id,
        role="user",
        content=user_message,
    )

    run = client.agents.create_run(
        thread_id=thread_id,
        agent_id=agent_id,
    )
    logger.info("Run created: %s (status: %s)", run.id, run.status)

    while True:
        status_str = str(run.status).lower()
        if "completed" in status_str: break
        if any(kw in status_str for kw in ["failed", "cancelled", "expired"]): 
            break
        
        if "requires_action" in status_str:
            tool_outputs = []
            submit_data = getattr(run, 'required_action', None)
            if submit_data and hasattr(submit_data, 'submit_tool_outputs'):
                for tool_call in submit_data.submit_tool_outputs.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = json.loads(tool_call.function.arguments)
                    logger.info("Agent calling tool: %s", fn_name)
                    output = _dispatch_tool(fn_name, fn_args)
                    tool_outputs.append({"tool_call_id": tool_call.id, "output": output})

                client.agents.submit_tool_outputs_to_run(
                    thread_id=thread_id, run_id=run.id, tool_outputs=tool_outputs
                )
                run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
                continue

        time.sleep(2)
        run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
        logger.info("Run status: %s", run.status)

    if "failed" in str(run.status).lower():
        last_error = getattr(run, "last_error", None)
        error_msg  = getattr(last_error, "message", str(last_error))
        raise RuntimeError(f"Agent run failed: {error_msg}")

    all_messages = list(client.agents.list_messages(thread_id=thread_id))
    for msg in all_messages:
        if msg.role == "assistant":
            content = getattr(msg, "content", [])
            if isinstance(content, list):
                return "".join(block.text.value for block in content if hasattr(block, "text"))
            return str(content)

    return "(No response from agent)"


def _dispatch_tool(fn_name: str, fn_args: dict) -> str:
    import tools
    fn = getattr(tools, fn_name, None)
    if fn is None: return json.dumps({"error": f"Unknown tool: {fn_name}"})
    try:
        return fn(**fn_args)
    except Exception as exc:
        logger.exception("Tool %s error", fn_name)
        return json.dumps({"error": str(exc)})


def validate_narrative(project_name: str, narrative_text: str, period: str, thread_id: Optional[str] = None) -> dict:
    client = _get_project_client()
    agent  = _get_or_create_agent(client)

    if not thread_id:
        thread = client.agents.create_thread()
        thread_id = thread.id

    prompt = f"Please validate the narrative for **{project_name}** ({period}):\n\n{narrative_text}"
    result = _run_and_wait(client, agent.id, thread_id, prompt)
    return {"thread_id": thread_id, "validation_result": result}
