"""
agent/create_agent.py — One-time setup script to create the NDA Narrative Validator agent
in Azure AI Foundry.

Run this ONCE from the agent/ directory:

    venv\\Scripts\\python create_agent.py

What it does:
  1. Connects to your Azure AI Foundry project using the same client as agent_runner.py.
  2. Checks if 'nda-narrative-validator-v3' already exists — skips creation if so.
  3. Creates the agent with the correct name, system prompt, and function tools.

Required env vars (loaded from local.settings.json automatically):
    AZURE_FOUNDRY_PROJECT_ENDPOINT
    AZURE_FOUNDRY_MODEL_DEPLOYMENT
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

# ── Load local.settings.json ─────────────────────────────────────────────────
settings_path = pathlib.Path(__file__).parent / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for key, value in json.load(f).get("Values", {}).items():
            os.environ.setdefault(key, value)
    print(f"Loaded settings from {settings_path}\n")
else:
    print("local.settings.json not found — relying on environment variables\n")

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from config import PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME
from system_prompt import get_system_prompt
from agent_runner import _get_project_client, _build_chat_completions_tools

AGENT_NAME = "nda-narrative-validator-v3"


def create_agent() -> None:
    print(f"Endpoint : {PROJECT_ENDPOINT}")
    print(f"Model    : {MODEL_DEPLOYMENT_NAME}")
    print(f"Agent    : {AGENT_NAME}\n")

    client        = _get_project_client()
    openai_client = client.get_openai_client()

    # ── Check if agent already exists ─────────────────────────────────────────
    print("Checking for existing agent...")
    existing = None
    try:
        for assistant in openai_client.beta.assistants.list():
            if assistant.name == AGENT_NAME:
                existing = assistant
                break
    except Exception as exc:
        print(f"Could not list agents: {exc}")
        sys.exit(1)

    if existing:
        print(f"Agent '{AGENT_NAME}' already exists.")
        print(f"  ID    : {existing.id}")
        print(f"  Model : {existing.model}")
        print("\nNo action taken. To recreate: delete it via portal then re-run this script.")
        return

    # ── Build tools and system prompt ─────────────────────────────────────────
    print("Building function tools...")
    tools = _build_chat_completions_tools()

    print("Loading system prompt...")
    instructions = get_system_prompt()
    print(f"System prompt: {len(instructions)} chars / ~{len(instructions)//4} tokens")

    # ── Create the agent ───────────────────────────────────────────────────────
    print(f"\nCreating agent '{AGENT_NAME}'...")
    agent = openai_client.beta.assistants.create(
        name=AGENT_NAME,
        model=MODEL_DEPLOYMENT_NAME,
        instructions=instructions,
        tools=tools,
    )

    print(f"\nAgent created successfully!")
    print(f"  Name  : {agent.name}")
    print(f"  ID    : {agent.id}")
    print(f"  Model : {agent.model}")
    print(f"\nNext steps:")
    print(f"  1. Refresh AI Foundry portal — '{AGENT_NAME}' should now appear in Agents.")
    print(f"  2. Deploy the function app: func azure functionapp publish nda-foundry-api")
    print(f"  3. No code changes needed — agent_runner.py references it by name.")


if __name__ == "__main__":
    create_agent()