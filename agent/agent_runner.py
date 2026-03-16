"""
agent/agent_runner.py — Next Gen Foundry SDK (2.x) Version

This version uses the azure-ai-projects >= 2.0.0 SDK which targets the
"New Foundry" experience.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    Agent,
    AzureAISearchTool,
    FunctionTool,
    ToolSet,
    RunStatus,
)
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
)
from system_prompt import get_system_prompt
from tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# Change AGENT_NAME to force a new agent in the "New Foundry" portal
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


def _get_or_create_agent(client: AIProjectClient) -> Agent:
    """
    Returns an existing agent, or creates a new one using the ToolSet pattern (SDK 2.x).
    """
    # Check for existing agent
    for agent in client.agents.list_agents():
        if agent.name == AGENT_NAME:
            logger.info("Reusing existing agent: %s (%s)", agent.name, agent.id)
            return agent

    logger.info("Creating new agent: %s", AGENT_NAME)

    use_ai_search = os.environ.get("USE_AI_SEARCH", "false").lower() == "true"
    
    # In SDK 2.x, we use a ToolSet to group tools
    toolset = ToolSet()

    # 1. Search Tool
    if use_ai_search:
        search_tool = AzureAISearchTool(
            index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
            index_name=AZURE_SEARCH_INDEX_NAME,
        )
        toolset.add_tool(search_tool)
        logger.info("AI Search tool ADDED to toolset")

    # 2. Function Tools
    function_tool = FunctionTool(functions=AGENT_TOOLS)
    toolset.add_tool(function_tool)
    logger.info("Function tools ADDED to toolset")

    agent = client.agents.create_agent(
        model=MODEL_DEPLOYMENT_NAME,
        name=AGENT_NAME,
        instructions=get_system_prompt(),
        toolset=toolset,
    )
    logger.info("Agent created: %s", agent.id)
    return agent


def _run_and_wait(client: AIProjectClient, agent_id: str, thread_id: str, user_message: str) -> str:
    # Add user message
    client.agents.create_message(
        thread_id=thread_id,
        role="user",
        content=user_message,
    )

    # Create run (SDK 2.x uses create_run directly on agents)
    run = client.agents.create_run(
        thread_id=thread_id,
        agent_id=agent_id,
    )
    logger.info("Run created: %s (status: %s)", run.id, run.status)

    while run.status in (RunStatus.QUEUED, RunStatus.IN_PROGRESS, RunStatus.REQUIRES_ACTION):
        time.sleep(2)
        run = client.agents.get_run(thread_id=thread_id, run_id=run.id)
        logger.info("Run status: %s", run.status)

        if run.status == RunStatus.REQUIRES_ACTION:
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

    if run.status == RunStatus.FAILED:
        last_error = getattr(run, "last_error", None)
        error_code = getattr(last_error, "code", "unknown")
        error_msg  = getattr(last_error, "message", str(last_error))
        logger.error("Run FAILED — code: %s | message: %s", error_code, error_msg)
        raise RuntimeError(f"Agent run failed [{error_code}]: {error_msg}")

    # Get response
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
