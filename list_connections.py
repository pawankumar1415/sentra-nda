"""
list_connections.py - Print all connections in the Foundry project so you can
copy the correct connection ID into agent/local.settings.json.

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
print(f"{'Name':<40} {'Type':<30} {'ID (use this!)'}")
print("-" * 120)

for conn in client.connections.list():
    print(f"{conn.name:<40} {conn.connection_type:<30} {conn.id}")

print("\n✅ Copy the 'ID' value for your AI Search connection")
print("   into AZURE_AI_SEARCH_CONNECTION_NAME in agent/local.settings.json")
