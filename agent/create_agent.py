"""
agent/create_agent.py — One-time setup script to create the NDA Narrative Validator agent.

Run from the agent/ directory:
    venv\\Scripts\\python create_agent.py
"""

import json, os, pathlib, sys

# ── Load local.settings.json ─────────────────────────────────────────────────
settings = pathlib.Path(__file__).parent / "local.settings.json"
if settings.exists():
    for k, v in json.load(open(settings)).get("Values", {}).items():
        os.environ.setdefault(k, v)
    print(f"Loaded settings from {settings}\n")

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import FunctionTool
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from config import PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME
from system_prompt import get_system_prompt
from tools import check_eac_variance, list_projects_with_material_movements

AGENT_NAME = "nda-narrative-validator-v3"

# ── Credential ────────────────────────────────────────────────────────────────
tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
client_id     = os.environ.get("AZURE_CLIENT_ID", "")
client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")

if tenant_id and client_id and client_secret and "<" not in client_id:
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    print("Auth: Service Principal")
else:
    credential = DefaultAzureCredential()
    print("Auth: DefaultAzureCredential (az login)")

print(f"Endpoint : {PROJECT_ENDPOINT}")
print(f"Model    : {MODEL_DEPLOYMENT_NAME}")
print(f"Agent    : {AGENT_NAME}\n")

client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)

# ── Check if already exists ───────────────────────────────────────────────────
print("Checking for existing agent...")
for agent in client.list_agents():
    if agent.name == AGENT_NAME:
        print(f"Agent '{AGENT_NAME}' already exists — id={agent.id}")
        print("Nothing to do.")
        sys.exit(0)

# ── Build tools ───────────────────────────────────────────────────────────────
ft = FunctionTool(functions={check_eac_variance, list_projects_with_material_movements})

# ── Load system prompt ────────────────────────────────────────────────────────
instructions = get_system_prompt()
print(f"System prompt: {len(instructions)} chars")

# ── Create agent ──────────────────────────────────────────────────────────────
print(f"\nCreating agent '{AGENT_NAME}'...")
agent = client.create_agent(
    model=MODEL_DEPLOYMENT_NAME,
    name=AGENT_NAME,
    instructions=instructions,
    tools=ft.definitions,
)

print(f"\nDone!")
print(f"  Name : {agent.name}")
print(f"  ID   : {agent.id}")
print(f"\nRefresh AI Foundry portal — the agent should now appear in Agents.")
print(f"Then redeploy: func azure functionapp publish nda-foundry-api")