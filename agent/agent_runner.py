"""
agent/agent_runner.py — Ultra-Resilient Next Gen SDK Version

This version uses a multi-fallback approach for listing agents and status 
checking to handle the shifting API of the azure-ai-projects 2.x preview SDK.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

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
        # Check for name (some SDK versions use 'name' property, others 'display_name')
        name_val = getattr(agent, "name", None) or getattr(agent, "display_name", None)
        if name_val == AGENT_NAME:
            logger.info("Reusing existing agent: %s (%s)", name_val, agent.id)
            return agent

    logger.info("Creating new agent: %s", AGENT_NAME)
    use_ai_search = os.environ.get("USE_AI_SEARCH", "false").lower() == "true"
    
    tools_list = []
    
    # 1. Search Tool
    SearchToolClass = getattr(models, "AzureAISearchTool", None) or getattr(models, "AISearchTool", None)
    if use_ai_search and SearchToolClass:
        tools_list.append(SearchToolClass(
            index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
            index_name=AZURE_SEARCH_INDEX_NAME,
        ))

    # 2. Function Tools
    FunctionToolClass = getattr(models, "FunctionTool", None)
    if FunctionToolClass:
        tools_list.append(FunctionToolClass(functions=AGENT_TOOLS))

    create_kwargs = {
        "model": MODEL_DEPLOYMENT_NAME,
        "name": AGENT_NAME,
        "instructions": get_system_prompt(),
    }

    # Handle ToolSet if it exists
    ToolSetClass = getattr(models, "ToolSet", None) or getattr(models, "Toolset", None)
    if ToolSetClass:
        ts = ToolSetClass()
        for t in tools_list:
            if hasattr(ts, 'add_tool'): ts.add_tool(t)
            elif hasattr(ts, 'tools'): ts.tools.append(t)
        create_kwargs["toolset"] = ts
    else:
        create_kwargs["tools"] = tools_list

    agent = client.agents.create_agent(**create_kwargs)
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

    # LOOP UNTIL TERMINAL STATE
    # Terminal states: completed, failed, cancelled, expired
    # Active states: queued, in_progress, requires_action, cancelling
    while True:
        status_str = str(run.status).lower()
        if "completed" in status_str: break
        if "failed" in status_str or "cancelled" in status_str or "expired" in status_str: break
        
        if "requires_action" in status_str:
            tool_outputs = []
            for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)
                logger.info("Agent calling tool: %s", fn_name)
                output = _dispatch_tool(fn_name, fn_args)
                tool_outputs.append({"tool_call_id": tool_call.id, "output": output})

            client.agents.submit_tool_outputs_to_run(
                thread_id=thread_id, run_id=run.id, tool_outputs=tool_outputs
            )
            # Re-fetch immediately after tool submission
            run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
            continue

        time.sleep(2)
        run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
        logger.info("Run status: %s", run.status)

    if "failed" in str(run.status).lower():
        last_error = getattr(run, "last_error", None)
        error_msg  = getattr(last_error, "message", str(last_error))
        raise RuntimeError(f"Agent run failed: {error_msg}")

    # List messages
    all_messages = list(client.agents.list_messages(thread_id=thread_id))
    # Some SDKs return newest first, some oldest first. We want the latest assistant message.
    for msg in all_messages:
        if msg.role == "assistant":
            return "".join(block.text.value for block in msg.content if hasattr(block, "text"))

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
