"""
agent/agent_runner.py — Ultra-Resilient Next Gen SDK Version

This version correctly handles the deeply nested AzureAISearchTool structure 
required by more recent 2.x Beta SDKs, while maintaining fallbacks.
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
                # Some return iterators, some lists
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
                # v2.0.0b4 nested way
                index_resource = IndexResourceClass(
                    project_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                    index_name=AZURE_SEARCH_INDEX_NAME
                )
                tool_resource = ToolResourceClass(indexes=[index_resource])
                tools_list.append(SearchToolClass(azure_ai_search=tool_resource))
                logger.info("Search tool added (nested structure)")
            except Exception as e:
                logger.warning("Failed to create nested SearchTool: %s. Trying flat structure...", e)
                # Fallback to flat way (older 2.x versions)
                try:
                    tools_list.append(SearchToolClass(
                        index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                        index_name=AZURE_SEARCH_INDEX_NAME
                    ))
                    logger.info("Search tool added (flat structure)")
                except Exception as e2:
                    logger.error("Failed to add Search tool entirely: %s", e2)
        elif SearchToolClass:
            # Maybe it's a version that takes flat args but doesn't have Resource classes
            try:
                tools_list.append(SearchToolClass(
                    index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                    index_name=AZURE_SEARCH_INDEX_NAME
                ))
                logger.info("Search tool added (flat fallback)")
            except Exception as e:
                logger.error("Failed to add Search tool: %s", e)

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

    while True:
        status_str = str(run.status).lower()
        
        # Terminal states
        if "completed" in status_str: break
        if "failed" in status_str or "cancelled" in status_str or "expired" in status_str: 
            break
        
        # Action required
        if "requires_action" in status_str:
            tool_outputs = []
            if hasattr(run, 'required_action') and run.required_action:
                for tool_call in run.required_action.submit_tool_outputs.tool_calls:
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

    # List messages
    all_messages = list(client.agents.list_messages(thread_id=thread_id))
    for msg in all_messages:
        if msg.role == "assistant":
            # The structure might be list of blocks or just content
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
