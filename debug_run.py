"""
debug_run.py - Test whether an agent RUN works at all.

Tests in order:
  1. Agent with NO tools — bare GPT call via Agents API
  2. Agent with AI Search only
  3. Agent with AI Search + Function tools

Run: venv\Scripts\python debug_run.py
"""
import json, os, pathlib, time, logging

logging.basicConfig(level=logging.WARNING)   # keep output clean

settings_path = pathlib.Path(__file__).parent / "agent" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for k, v in json.load(f).get("Values", {}).items():
            os.environ.setdefault(k, v)

from agent.config import (
    PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME,
    AZURE_SEARCH_CONNECTION_NAME, AZURE_SEARCH_INDEX_NAME,
)
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.ai.agents.models import AzureAISearchTool, FunctionTool, RunStatus

# ── Auth ──────────────────────────────────────────────────────────────────────
tid, cid, cs = (os.environ.get(k,"") for k in ("AZURE_TENANT_ID","AZURE_CLIENT_ID","AZURE_CLIENT_SECRET"))
cred = ClientSecretCredential(tid,cid,cs) if (tid and cid and cs) else DefaultAzureCredential()
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred)
agents = client.agents


def run_test(label, agent_id):
    """Create a thread, send one message, wait for result."""
    thread = agents.threads.create()
    agents.messages.create(thread_id=thread.id, role="user",
                           content="Say hello in one sentence.")
    run = agents.runs.create(thread_id=thread.id, agent_id=agent_id)

    for _ in range(30):
        time.sleep(2)
        run = agents.runs.get(thread_id=thread.id, run_id=run.id)
        if run.status not in (RunStatus.QUEUED, RunStatus.IN_PROGRESS):
            break

    if run.status == RunStatus.FAILED:
        err = getattr(run, "last_error", None)
        print(f"  ❌ FAILED — code: {getattr(err,'code','?')} | {getattr(err,'message',err)}")
    else:
        msgs = list(agents.messages.list(thread_id=thread.id))
        text = next(
            ("".join(b.text.value for b in m.content if hasattr(b,"text"))
             for m in msgs if m.role == "assistant"),
            "(no reply)"
        )
        print(f"  ✅ SUCCESS — reply: {text[:120]}")

    agents.threads.delete(thread.id)


# ── TEST 1: No tools ──────────────────────────────────────────────────────────
print("="*55)
print("TEST 1 — Agent with NO tools")
print("="*55)
a1 = agents.create_agent(model=MODEL_DEPLOYMENT_NAME, name="dbg-no-tools",
                          instructions="You are a helpful assistant.")
run_test("no-tools", a1.id)
agents.delete_agent(a1.id)


# ── TEST 2: AI Search only ────────────────────────────────────────────────────
print("\n" + "="*55)
print("TEST 2 — Agent with AI Search only")
print("="*55)
ai_search = AzureAISearchTool(index_connection_id=AZURE_SEARCH_CONNECTION_NAME,
                               index_name=AZURE_SEARCH_INDEX_NAME)
a2 = agents.create_agent(model=MODEL_DEPLOYMENT_NAME, name="dbg-search-only",
                          instructions="You are a helpful assistant.",
                          tools=ai_search.definitions,
                          tool_resources=ai_search.resources)
run_test("search-only", a2.id)
agents.delete_agent(a2.id)


# ── TEST 3: Function tools only ───────────────────────────────────────────────
print("\n" + "="*55)
print("TEST 3 — Agent with Function tools only")
print("="*55)
from agent.tools import AGENT_TOOLS
fn_tool = FunctionTool(functions=AGENT_TOOLS)

print(f"  Function schemas being registered:")
for d in fn_tool.definitions:
    fn = getattr(d, "function", None)
    print(f"    - {getattr(fn,'name','?')}: {str(getattr(fn,'parameters','?'))[:80]}")

a3 = agents.create_agent(model=MODEL_DEPLOYMENT_NAME, name="dbg-fn-only",
                          instructions="You are a helpful assistant.",
                          tools=fn_tool.definitions)
run_test("fn-only", a3.id)
agents.delete_agent(a3.id)


print("\n" + "="*55)
print("DONE")
print("="*55)
