"""
agent/create_agent.py — One-time setup script to create the NDA Narrative Validator agent
in Azure AI Foundry.

Run this ONCE from the agent/ directory:

    venv\\Scripts\\python create_agent.py

What it does:
  1. Connects to your Azure AI Foundry project.
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

# Add agent/ to path
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FunctionTool
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from config import PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME
from system_prompt import get_system_prompt
from tools import check_eac_variance, list_projects_with_material_movements

AGENT_NAME = "nda-narrative-validator-v3"


def _get_credential():
    tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
    client_id     = os.environ.get("AZURE_CLIENT_ID", "")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
    if tenant_id and client_id and client_secret and "<" not in client_id:
        print(f"Auth: Service Principal (client_id={client_id})")
        return ClientSecretCredential(tenant_id, client_id, client_secret)
    print("Auth: DefaultAzureCredential (az login / Managed Identity)")
    return DefaultAzureCredential()


def create_agent() -> None:
    credential = _get_credential()
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    print(f"Endpoint : {PROJECT_ENDPOINT}")
    print(f"Model    : {MODEL_DEPLOYMENT_NAME}")
    print(f"Agent    : {AGENT_NAME}\n")

    # ── Check if agent already exists ─────────────────────────────────────────
    print("Checking for existing agent...")
    existing = None
    try:
        for agent in client.agents.list():
            if agent.name == AGENT_NAME:
                existing = agent
                break
    except Exception as exc:
        print(f"Could not list agents: {exc}")
        print("Ensure 'Azure AI Developer' role is assigned to your identity on the Foundry project.")
        sys.exit(1)

    if existing:
        print(f"Agent '{AGENT_NAME}' already exists.")
        print(f"  ID    : {existing.id}")
        print(f"  Model : {existing.model}")
        print("\nNo action taken. To recreate: delete it via portal then re-run this script.")
        return

    # ── Build tool definitions ─────────────────────────────────────────────────
    print("Building function tools...")
    ft = FunctionTool(functions={check_eac_variance, list_projects_with_material_movements})

    # ── Load system prompt ─────────────────────────────────────────────────────
    print("Loading system prompt...")
    instructions = get_system_prompt()
    print(f"System prompt: {len(instructions)} chars / ~{len(instructions)//4} tokens")

    # ── Create the agent ───────────────────────────────────────────────────────
    print(f"\nCreating agent '{AGENT_NAME}'...")
    agent = client.agents.create(
        model=MODEL_DEPLOYMENT_NAME,
        name=AGENT_NAME,
        instructions=instructions,
        tools=ft.definitions,
        description=(
            "NDA Narrative Validation Agent — validates project narrative text "
            "against NDA Good Practice Guidelines (Layer 1) and EAC/schedule "
            "variance data (Layer 2). Used by the BSBI custom solution."
        ),
    )

    print(f"\nAgent created successfully!")
    print(f"  Name  : {agent.name}")
    print(f"  ID    : {agent.id}")
    print(f"  Model : {agent.model}")
    print(f"\nNext steps:")
    print(f"  1. Go to AI Foundry portal — '{AGENT_NAME}' should now appear in the Agents list.")
    print(f"  2. Deploy the function app: func azure functionapp publish nda-foundry-api")
    print(f"  3. The agent_reference in agent_runner.py resolves by name — no code changes needed.")


if __name__ == "__main__":
    create_agent()