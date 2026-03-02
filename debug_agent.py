"""
debug_agent.py - Minimal diagnostic script to find the exact issue with create_agent.

Run on the client machine:
    venv\Scripts\python debug_agent.py
"""
import json
import logging
import os
import pathlib

# ── Load credentials ──────────────────────────────────────────────────────────
settings_path = pathlib.Path(__file__).parent / "agent" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for key, value in json.load(f).get("Values", {}).items():
            os.environ.setdefault(key, value)
    print(f"✅ Loaded credentials from {settings_path}")

# Enable HTTP-level logging to see exactly what is sent to Azure
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.DEBUG)

from agent.config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME,
    AZURE_SEARCH_INDEX_NAME,
)

print("\n" + "="*60)
print("CONFIG CHECK")
print("="*60)
print(f"  PROJECT_ENDPOINT          : {PROJECT_ENDPOINT}")
print(f"  MODEL_DEPLOYMENT_NAME     : {MODEL_DEPLOYMENT_NAME}")
print(f"  AZURE_SEARCH_CONNECTION   : {AZURE_SEARCH_CONNECTION_NAME}")
print(f"  AZURE_SEARCH_INDEX        : {AZURE_SEARCH_INDEX_NAME}")

# ── Inspect what AzureAISearchTool produces ───────────────────────────────────
print("\n" + "="*60)
print("TOOL INSPECTION")
print("="*60)

from azure.ai.agents.models import AzureAISearchTool, FunctionTool

ai_search = AzureAISearchTool(
    index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
    index_name=AZURE_SEARCH_INDEX_NAME,
)

print("\nai_search.definitions:")
for d in ai_search.definitions:
    print(f"  {d}")

print("\nai_search.resources:")
print(f"  {ai_search.resources}")
print(f"  type: {type(ai_search.resources)}")

# ── Try create_agent step by step ────────────────────────────────────────────
print("\n" + "="*60)
print("STEP 1 — Connect to AI Project")
print("="*60)

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential

tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
client_id     = os.environ.get("AZURE_CLIENT_ID", "")
client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")

if tenant_id and client_id and client_secret:
    credential = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    print("Auth: ClientSecretCredential")
else:
    credential = DefaultAzureCredential()
    print("Auth: DefaultAzureCredential (az login)")

client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
print("✅ AIProjectClient created")

# ── Try 1: Search tool only ───────────────────────────────────────────────────
print("\n" + "="*60)
print("STEP 2 — Try create_agent with AI Search only")
print("="*60)
try:
    agent = client.agents.create_agent(
        model=MODEL_DEPLOYMENT_NAME,
        name="nda-debug-search-only",
        instructions="Test agent",
        tools=ai_search.definitions,
        tool_resources=ai_search.resources,
    )
    print(f"✅ SUCCESS with AI Search only — agent id: {agent.id}")
    client.agents.delete_agent(agent.id)
    print("   (cleaned up)")
    search_only_works = True
except Exception as e:
    print(f"❌ FAILED: {e}")
    search_only_works = False

# ── Try 2: Function tool only ─────────────────────────────────────────────────
print("\n" + "="*60)
print("STEP 3 — Try create_agent with Function tools only")
print("="*60)
from agent.tools import AGENT_TOOLS
function_tool = FunctionTool(functions=AGENT_TOOLS)

print(f"\nfunction_tool.definitions: {len(function_tool.definitions)} tools")
for d in function_tool.definitions:
    print(f"  - {getattr(d, 'function', d)}")

try:
    agent = client.agents.create_agent(
        model=MODEL_DEPLOYMENT_NAME,
        name="nda-debug-functions-only",
        instructions="Test agent",
        tools=function_tool.definitions,
    )
    print(f"✅ SUCCESS with Function tools only — agent id: {agent.id}")
    client.agents.delete_agent(agent.id)
    print("   (cleaned up)")
    functions_only_works = True
except Exception as e:
    print(f"❌ FAILED: {e}")
    functions_only_works = False

# ── Try 3: Both combined ──────────────────────────────────────────────────────
print("\n" + "="*60)
print("STEP 4 — Try create_agent with BOTH combined")
print("="*60)
if search_only_works and functions_only_works:
    try:
        agent = client.agents.create_agent(
            model=MODEL_DEPLOYMENT_NAME,
            name="nda-debug-combined",
            instructions="Test agent",
            tools=ai_search.definitions + function_tool.definitions,
            tool_resources=ai_search.resources,
        )
        print(f"✅ SUCCESS with combined tools — agent id: {agent.id}")
        client.agents.delete_agent(agent.id)
        print("   (cleaned up)")
    except Exception as e:
        print(f"❌ FAILED combined: {e}")
else:
    print("Skipped — one of the individual tests already failed")

print("\n" + "="*60)
print("DIAGNOSIS COMPLETE")
print("="*60)
