"""
debug_agent_permissions.py

Tests whether the Azure AI Foundry permissions are correctly applied by
attempting each authenticated operation independently. Run with:
    venv\Scripts\python debug_agent_permissions.py

If you see ✅ for all steps, you are ready to run with USE_AI_SEARCH=true.
"""

import json
import os
import pathlib

# ── Load credentials from agent/local.settings.json ──────────────────────────
settings_path = pathlib.Path(__file__).parent / "agent" / "local.settings.json"
if settings_path.exists():
    values = json.load(open(settings_path))["Values"]
    for k, v in values.items():
        os.environ.setdefault(k, v)
    print(f"✅ Loaded credentials from {settings_path}\n")
else:
    print(f"⚠️  {settings_path} not found — falling back to environment variables\n")

# ── Choose credential ─────────────────────────────────────────────────────────
from azure.identity import DefaultAzureCredential, ClientSecretCredential

tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
client_id     = os.environ.get("AZURE_CLIENT_ID", "")
client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
USE_SP        = bool(tenant_id and client_id and client_secret and "<" not in client_id)

if USE_SP:
    cred = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    print(f"Auth mode: Service Principal (client_id={client_id})")
else:
    cred = DefaultAzureCredential()
    print("Auth mode: DefaultAzureCredential (az login / VS Code)")

print("-" * 55)


# ── TEST 1: Get a token for Azure AI Foundry ──────────────────────────────────
print("\n[1] Azure AI Foundry — Requesting access token...")
try:
    token = cred.get_token("https://ai.azure.com/.default")
    print(f"  ✅ Token obtained (expires at {token.expires_on})")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    print("     → Ensure 'Azure AI Developer' role is assigned to the identity on the Foundry project")


# ── TEST 2: Connect to AI Foundry Project ─────────────────────────────────────
print("\n[2] Azure AI Foundry — Connecting to project...")
endpoint = os.environ.get("AZURE_FOUNDRY_PROJECT_ENDPOINT", "")
if not endpoint or "<" in endpoint:
    print("  ⚠️  SKIPPED — AZURE_FOUNDRY_PROJECT_ENDPOINT is not set in local.settings.json")
else:
    try:
        from azure.ai.projects import AIProjectClient
        client = AIProjectClient(endpoint=endpoint, credential=cred)
        print(f"  ✅ AIProjectClient created for {endpoint}")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")


# ── TEST 3: List existing agents ──────────────────────────────────────────────
print("\n[3] Azure AI Foundry — Listing agents (requires Azure AI Developer role)...")
try:
    agents = list(client.agents.list())
    print(f"  ✅ Found {len(agents)} agent(s) in the project:")
    for a in agents:
        print(f"      • {a.name} (id={a.id})")
    if not agents:
        print("     (No agents created yet — that's fine)")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    print("     → This usually means the 'Azure AI Developer' role is MISSING")


# ── TEST 4: Azure AI Search Token ─────────────────────────────────────────────
print("\n[4] Azure AI Search — Requesting access token...")
try:
    search_token = cred.get_token("https://search.azure.com/.default")
    print(f"  ✅ Search token obtained (expires at {search_token.expires_on})")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    print("     → Ensure 'Search Index Data Reader' role is assigned on the Search resource")


# ── TEST 5: Query the AI Search Index ─────────────────────────────────────────
print("\n[5] Azure AI Search — Querying nda-mppr-projects index...")
search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
search_index    = os.environ.get("AZURE_SEARCH_INDEX_NAME", "nda-mppr-projects")

if not search_endpoint or "<" in search_endpoint:
    print("  ⚠️  SKIPPED — AZURE_SEARCH_ENDPOINT is not set in local.settings.json")
else:
    try:
        from azure.search.documents import SearchClient
        search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=search_index,
            credential=cred,
        )
        results = list(search_client.search("*", top=1))
        print(f"  ✅ Search query succeeded — {len(results)} result(s) returned")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        print("     → Ensure 'Search Index Data Reader' role is assigned to the identity on the Search resource")


print("\n" + "=" * 55)
print("Permission check complete. Fix any ❌ before running the agent with USE_AI_SEARCH=true.")
