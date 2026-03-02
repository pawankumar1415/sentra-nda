"""
agent/agent_runner.py — Main entry point for the NDA Narrative Validation Agent.

This module creates the Azure AI Foundry Agent, manages threads, and exposes
three high-level functions that map directly to the user stories:

    validate_narrative(project_name, narrative_text, period)
        → NDA-01: Validate a single project narrative (Layer 1 + Layer 2)

    batch_validate(period_short_name)
        → NDA-002: Validate all projects with material movements in a period

    generate_narrative(project_name, period, key_points)
        → NDA-003: Generate a Good Practice-compliant narrative draft

Usage (interactive / script):
    python -m agent.agent_runner

Usage (as a module imported by the API / Bot layer):
    from agent.agent_runner import validate_narrative, batch_validate, generate_narrative
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import (
    AzureAISearchTool,
    FunctionTool,
    ToolSet,
    RunStatus,
)
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from agent.config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
)
from agent.system_prompt import get_system_prompt
from agent.tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration: Agent name and version
# Change AGENT_NAME to force a new agent (old one stays in Foundry but unused).
# ─────────────────────────────────────────────────────────────────────────────
AGENT_NAME = "nda-narrative-validator-v1"


# ─────────────────────────────────────────────────────────────────────────────
# Shared project client (initialised once per process)
# ─────────────────────────────────────────────────────────────────────────────
def _get_project_client() -> AIProjectClient:
    """
    Returns an authenticated AIProjectClient.

    AIProjectClient requires a TokenCredential (OAuth), not a simple API key.
    Priority order:
      1. Service Principal  — if AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID are set
      2. Azure CLI          — run `az login` once (recommended for local dev)
      3. VS Code identity   — if signed into Azure via VS Code extension
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
        logger.info("Auth: using ClientSecretCredential (service principal)")
    else:
        credential = DefaultAzureCredential()
        logger.info("Auth: using DefaultAzureCredential (az login / VS Code / managed identity)")

    return AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent creation / retrieval
# ─────────────────────────────────────────────────────────────────────────────
def _get_or_create_agent(client: AIProjectClient):
    """
    Returns an existing agent with AGENT_NAME, or creates a new one.
    This avoids creating a new agent on every call (agents persist in Foundry).
    """
    agents_client = client.agents

    # Check if an agent with this name already exists
    # list_agents() returns an ItemPaged iterator — iterate directly
    for agent in agents_client.list_agents():
        if agent.name == AGENT_NAME:
            logger.info("Reusing existing agent: %s (%s)", agent.name, agent.id)
            return agent

    logger.info("Creating new agent: %s", AGENT_NAME)

    # ── Tool 1: Azure AI Search (knowledge base) ──────────────────────────
    ai_search_tool = AzureAISearchTool(
        index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
        index_name=AZURE_SEARCH_INDEX_NAME,
    )

    # ── Tool 2: Python Function Tools (EAC variance + batch listing) ──────
    function_tool = FunctionTool(functions=AGENT_TOOLS)

    # Build toolset and extract definitions + resources separately
    # (some SDK versions don't accept toolset= directly in create_agent)
    toolset = ToolSet()
    toolset.add(ai_search_tool)
    toolset.add(function_tool)

    agent = agents_client.create_agent(
        model=MODEL_DEPLOYMENT_NAME,
        name=AGENT_NAME,
        instructions=get_system_prompt(),
        tools=toolset.definitions,
        tool_resources=toolset.resources,
    )
    logger.info("Agent created: %s", agent.id)
    return agent


# ─────────────────────────────────────────────────────────────────────────────
# Thread management helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run_and_wait(client: AIProjectClient, agent_id: str, thread_id: str, user_message: str) -> str:
    """
    Adds a user message to the thread, starts a run, polls until complete,
    handles any required tool calls, and returns the final assistant message.
    """
    agents_client = client.agents

    # Add user message
    agents_client.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_message,
    )

    # Create run
    run = agents_client.runs.create(
        thread_id=thread_id,
        assistant_id=agent_id,
    )

    # Poll for completion
    while run.status in (RunStatus.QUEUED, RunStatus.IN_PROGRESS, RunStatus.REQUIRES_ACTION):
        time.sleep(1)
        run = agents_client.runs.get(thread_id=thread_id, run_id=run.id)

        if run.status == RunStatus.REQUIRES_ACTION:
            # Handle function tool calls
            tool_outputs = []
            for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                logger.info("Agent calling tool: %s(%s)", fn_name, fn_args)

                # Dispatch to the correct Python function
                output = _dispatch_tool(fn_name, fn_args)
                tool_outputs.append({
                    "tool_call_id": tool_call.id,
                    "output": output,
                })

            agents_client.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=tool_outputs,
            )

    if run.status == RunStatus.FAILED:
        error_msg = getattr(run, "last_error", None)
        raise RuntimeError(f"Agent run failed: {error_msg}")

    # Get the latest assistant message
    # messages.list() returns an ItemPaged iterator — iterate directly
    all_messages = list(agents_client.messages.list(thread_id=thread_id))
    for msg in reversed(all_messages):
        if msg.role == "assistant":
            return "".join(
                block.text.value
                for block in msg.content
                if hasattr(block, "text")
            )

    return "(No response from agent)"

def _dispatch_tool(fn_name: str, fn_args: dict) -> str:
    """Dispatches a tool call by name to the correct Python function."""
    from agent import tools
    fn = getattr(tools, fn_name, None)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {fn_name}"})
    try:
        return fn(**fn_args)
    except Exception as exc:
        logger.exception("Tool %s raised an error: %s", fn_name, exc)
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def validate_narrative(
    project_name: str,
    narrative_text: str,
    period: str,
    thread_id: Optional[str] = None,
) -> dict:
    """
    NDA-01 — Validate a single project narrative.

    Performs Layer 1 (guidance) + Layer 2 (EAC/schedule data) validation.

    Args:
        project_name:   Name of the project (e.g. "Box Encapsulation Plant")
        narrative_text: The SRO narrative text from the Excel workbook
        period:         Reporting period (e.g. "P07" or "2025-P07")
        thread_id:      Optional — reuse an existing conversation thread

    Returns:
        {
            "thread_id": str,       ← pass this back to continue the conversation
            "validation_result": str ← formatted validation output from the agent
        }
    """
    client = _get_project_client()
    agent  = _get_or_create_agent(client)

    # Create or reuse a thread
    if not thread_id:
        thread = client.agents.threads.create()
        thread_id = thread.id

    prompt = f"""
Please validate the following project narrative for **{project_name}** (Reporting Period: **{period}**).

Perform both Layer 1 (guidance compliance) and Layer 2 (data-driven) validation.
For Layer 2, use the `check_eac_variance` tool to check for material EAC or schedule movements.

**Narrative Text:**
---
{narrative_text}
---
"""

    result = _run_and_wait(client, agent.id, thread_id, prompt)
    return {"thread_id": thread_id, "validation_result": result}


def batch_validate(period_short_name: str) -> dict:
    """
    NDA-002 — Batch validation for all projects with material movements in a period.

    Uses the `list_projects_with_material_movements` tool to identify which
    projects need narrative updates, then validates each one.

    Args:
        period_short_name: The period to check (e.g. "2025-P07")

    Returns:
        {
            "thread_id": str,
            "exceptions_report": str  ← formatted list of projects needing action
        }
    """
    client = _get_project_client()
    agent  = _get_or_create_agent(client)
    thread = client.agents.threads.create()

    prompt = f"""
I need a batch exceptions report for reporting period **{period_short_name}**.

Step 1: Use the `list_projects_with_material_movements` tool to identify all projects 
        with material EAC or schedule movements in this period.

Step 2: For each project returned, summarise:
        - The project name
        - The EAC variance amount and severity
        - The schedule variance in days
        - Whether narrative commentary is required
        - A one-line recommended action for the PMO

Format the output as a clear exceptions table sorted by severity (most significant first).
"""

    result = _run_and_wait(client, agent.id, thread.id, prompt)
    return {"thread_id": thread.id, "exceptions_report": result}


def generate_narrative(
    project_name: str,
    period: str,
    key_points: str,
    thread_id: Optional[str] = None,
) -> dict:
    """
    NDA-003 — Generate a Good Practice-compliant narrative draft.

    The agent uses the AI Search knowledge base to understand the project context,
    checks EAC variance data, then generates a compliant narrative using the
    Good Practice sentence templates.

    Args:
        project_name:   Name of the project
        period:         Reporting period (e.g. "P07")
        key_points:     Bullet points or sentences the reporter wants to include
                        (e.g. "DCA remains Amber, strike action delayed commissioning,
                         EAC increased by £1.2m due to inflation")
        thread_id:      Optional — reuse an existing conversation thread

    Returns:
        {
            "thread_id": str,
            "generated_narrative": str
        }
    """
    client = _get_project_client()
    agent  = _get_or_create_agent(client)

    if not thread_id:
        thread = client.agents.threads.create()
        thread_id = thread.id

    prompt = f"""
Please generate a Good Practice-compliant narrative for **{project_name}** (Period: **{period}**).

Use the AI Search knowledge base to understand the project's reported data, 
and call `check_eac_variance` to incorporate any material cost or schedule movements.

**Key points the reporter wants included:**
{key_points}

Generate a single flowing paragraph following all Good Practice sentence templates.
Do not use bullet points. Expand all acronyms on first use. Use full dates.
"""

    result = _run_and_wait(client, agent.id, thread_id, prompt)
    return {"thread_id": thread_id, "generated_narrative": result}


# ─────────────────────────────────────────────────────────────────────────────
# Interactive CLI (for local testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("NDA Narrative Validation Agent — Interactive Mode")
    print("=" * 60)
    print("Commands: 'validate', 'batch', 'generate', 'quit'")
    print()

    thread_id = None

    while True:
        cmd = input("Command > ").strip().lower()

        if cmd == "quit":
            break

        elif cmd == "validate":
            project = input("Project name > ").strip()
            period  = input("Period (e.g. 2025-P07) > ").strip()
            print("Paste narrative text (type END on a new line to finish):")
            lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            narrative = "\n".join(lines)

            print("\nValidating...\n")
            result = validate_narrative(project, narrative, period, thread_id)
            thread_id = result["thread_id"]
            print(result["validation_result"])

        elif cmd == "batch":
            period = input("Period to scan (e.g. 2025-P07) > ").strip()
            print("\nRunning batch validation...\n")
            result = batch_validate(period)
            print(result["exceptions_report"])

        elif cmd == "generate":
            project    = input("Project name > ").strip()
            period     = input("Period > ").strip()
            key_points = input("Key points to include > ").strip()

            print("\nGenerating narrative...\n")
            result = generate_narrative(project, period, key_points, thread_id)
            thread_id = result["thread_id"]
            print(result["generated_narrative"])

        else:
            print("Unknown command. Try: validate, batch, generate, quit")
