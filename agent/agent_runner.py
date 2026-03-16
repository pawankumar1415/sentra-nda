"""
agent/agent_runner.py — Highly Resilient Next Gen SDK Version

This version is designed to be compatible with multiple versions of the 
Azure AI Projects SDK 2.x, using flexible imports and string-based status checks.
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

# Agente Name v3
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
    Creates agent using whatever tool pattern the SDK supports.
    """
    for agent in client.agents.list_agents():
        if agent.name == AGENT_NAME:
            logger.info("Reusing existing agent: %s (%s)", agent.name, agent.id)
            return agent

    logger.info("Creating new agent: %s", AGENT_NAME)
    use_ai_search = os.environ.get("USE_AI_SEARCH", "false").lower() == "true"
    
    tools_list = []
    
    # 1. Search Tool
    if use_ai_search:
        # Some SDK versions use AzureAISearchTool, others AISearchTool
        SearchToolClass = getattr(models, "AzureAISearchTool", None) or getattr(models, "AISearchTool", None)
        if SearchToolClass:
            tools_list.append(SearchToolClass(
                index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                index_name=AZURE_SEARCH_INDEX_NAME,
            ))
            logger.info("Search tool added")

    # 2. Function Tools
    FunctionToolClass = getattr(models, "FunctionTool", None)
    if FunctionToolClass:
        tools_list.append(FunctionToolClass(functions=AGENT_TOOLS))
        logger.info("Function tools added")

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
            # Check if add_tool exists, otherwise use list
            if hasattr(ts, 'add_tool'):
                ts.add_tool(t)
            elif hasattr(ts, 'tools'):
                ts.tools.append(t)
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

    # Use string comparison for status to avoid Enum import issues
    # SDK usually returns strings or objects that serialize to strings (CamelCase or lowercase)
    while str(run.status).lower() in ("queued", "in_progress", "requires_action", "runstatus.queued", "runstatus.in_progress", "runstatus.requires_action"):
        time.sleep(2)
        run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
        logger.info("Run status: %s", run.status)

        if str(run.status).lower() in ("requires_action", "runstatus.requires_action"):
            tool_outputs = []
            for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                logger.info("Agent calling tool: %s(%s)", fn_name, fn_args)
                output = _dispatch_tool(fn_name, fn_args)
                tool_outputs.append({
                    "tool_call_id": tool_call.id,
                    "output": output,
                })

            client.agents.submit_tool_outputs_to_run(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=tool_outputs,
            )

    if str(run.status).lower() in ("failed", "runstatus.failed"):
        last_error = getattr(run, "last_error", None)
        error_code = getattr(last_error, "code", "unknown")
        error_msg  = getattr(last_error, "message", str(last_error))
        logger.error("Run FAILED — code: %s | message: %s", error_code, error_msg)
        raise RuntimeError(f"Agent run failed [{error_code}]: {error_msg}")

    all_messages = list(client.agents.list_messages(thread_id=thread_id))
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
