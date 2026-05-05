"""
agent/create_agent.py — One-time setup script to create the NDA Narrative Validator agent
in Azure AI Foundry (new Foundry experience).

Run from the agent/ directory:
    venv\\Scripts\\python create_agent.py           (create)
    venv\\Scripts\\python create_agent.py --delete  (delete and recreate)

The agent is created using the new Foundry Responses API pattern:
  project.agents.create_version() + PromptAgentDefinition
This makes it visible in the new Foundry portal and resolvable via agent_reference.
"""

import argparse, json, os, pathlib, sys

# ── Load local.settings.json ─────────────────────────────────────────────────
settings = pathlib.Path(__file__).parent / "local.settings.json"
if settings.exists():
    for k, v in json.load(open(settings)).get("Values", {}).items():
        os.environ.setdefault(k, v)
    print(f"Loaded settings from {settings}\n")

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential, ClientSecretCredential

from config import PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME
from system_prompt import get_system_prompt

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

project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

# ── Check if already exists ───────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--delete", action="store_true", help="Delete existing agent and recreate")
args = parser.parse_args()

print("Checking for existing agent...")
existing = None
for agent in project.agents.list():
    if agent.name == AGENT_NAME:
        existing = agent
        break

if existing and not args.delete:
    print(f"Agent '{AGENT_NAME}' already exists — version={existing.version}")
    print("Nothing to do. Run with --delete to recreate.")
    sys.exit(0)

if existing and args.delete:
    print(f"Deleting existing agent '{AGENT_NAME}' (version={existing.version})...")
    project.agents.delete(AGENT_NAME)
    print("Deleted.\n")

# ── Load system prompt ────────────────────────────────────────────────────────
instructions = get_system_prompt()
print(f"System prompt: {len(instructions)} chars")

# ── Create agent using new Foundry API ───────────────────────────────────────
print(f"\nCreating agent '{AGENT_NAME}' via create_version / PromptAgentDefinition...")
agent = project.agents.create_version(
    agent_name=AGENT_NAME,
    definition=PromptAgentDefinition(
        model=MODEL_DEPLOYMENT_NAME,
        instructions=instructions,
    ),
)

print(f"\nDone!")
print(f"  Name    : {agent.name}")
print(f"  Version : {agent.version}")
print(f"\nAgent is now resolvable via agent_reference in the Responses API.")
print(f"Refresh AI Foundry portal — '{AGENT_NAME}' should appear in Agents.")
print(f"Then redeploy: func azure functionapp publish nda-foundry-api")