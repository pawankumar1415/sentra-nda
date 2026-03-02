"""
list_connections.py - Print all connections in the Foundry project.

Run: venv\Scripts\python list_connections.py
"""
import json, os, pathlib

settings_path = pathlib.Path(__file__).parent / "agent" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for k, v in json.load(f).get("Values", {}).items():
            os.environ.setdefault(k, v)

from agent.config import PROJECT_ENDPOINT
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential

tid, cid, cs = (os.environ.get(k,"") for k in ("AZURE_TENANT_ID","AZURE_CLIENT_ID","AZURE_CLIENT_SECRET"))
cred = ClientSecretCredential(tid,cid,cs) if (tid and cid and cs) else DefaultAzureCredential()
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred)

print("Connections in this Foundry project:\n")
for conn in client.connections.list():
    print("=" * 60)
    # Dump every attribute the SDK gives us
    for k in dir(conn):
        if k.startswith("_") or callable(getattr(conn, k, None)):
            continue
        try:
            print(f"  {k}: {getattr(conn, k)}")
        except Exception:
            pass

print("\n✅ Copy the 'id' or 'name' value for your AI Search connection")
print("   into AZURE_AI_SEARCH_CONNECTION_NAME in agent/local.settings.json")
