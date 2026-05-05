"""
debug_agent_run.py — Minimal script to isolate what causes server_error in agent runs.

Tests in isolation:
  Step 1: Bare agent (no tools, no system prompt) — single "say hello" message
  Step 2: Agent with function tools only — simple question
  Step 3: Agent with the real system prompt — same simple question
  Step 4: Agent with system prompt + function tools — validate a sample narrative

Run:
    venv\\Scripts\\python debug_agent_run.py

Reads from agent/local.settings.json automatically.
"""

import json, logging, os, pathlib, time

# ── Load agent/local.settings.json ───────────────────────────────────────────
settings = pathlib.Path("agent/local.settings.json")
if settings.exists():
    for k, v in json.load(open(settings)).get("Values", {}).items():
        os.environ.setdefault(k, v)

logging.basicConfig(level=logging.WARNING)

from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FunctionTool, RunStatus
from azure.identity import DefaultAzureCredential, ClientSecretCredential

# ── Credential ────────────────────────────────────────────────────────────────
tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
client_id     = os.environ.get("AZURE_CLIENT_ID", "")
client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")

if tenant_id and client_id and client_secret:
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    print("Auth: ClientSecretCredential (service principal)")
else:
    credential = DefaultAzureCredential()
    print("Auth: DefaultAzureCredential (az login)")

endpoint   = os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"]
model      = os.environ.get("AZURE_FOUNDRY_MODEL_DEPLOYMENT", "gpt-5.1-chat")

print(f"Endpoint : {endpoint}")
print(f"Model    : {model}\n")

client = AIProjectClient(endpoint=endpoint, credential=credential)


# ── Helper: create agent, run, delete ────────────────────────────────────────
def run_test(label, tools, instructions, message):
    print(f"{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    # Create minimal agent
    kwargs = dict(model=model, name=f"debug-{int(time.time())}", instructions=instructions)
    if tools:
        kwargs["tools"] = tools
    agent = client.agents._create_agent(**kwargs)
    print(f"  Agent created: {agent.id}")

    try:
        thread = client.agents.threads.create()
        client.agents.messages.create(thread_id=thread.id, role="user", content=message)

        # Poll run
        run = client.agents.runs.create(thread_id=thread.id, agent_id=agent.id)
        for _ in range(30):
            time.sleep(2)
            run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)
            print(f"  Status: {run.status}", end="\r")
            if run.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
                               RunStatus.EXPIRED, "failed", "completed"):
                break

        print()
        if run.status == RunStatus.COMPLETED or run.status == "completed":
            msgs = list(client.agents.messages.list(thread_id=thread.id))
            response = next((m for m in msgs if m.role == "assistant"), None)
            text = response.content[0].text.value if response else "(no response)"
            print(f"  ✅ PASSED — Response: {text[:150]}")
            return True
        else:
            err = getattr(run, "last_error", None)
            print(f"  ❌ FAILED — status={run.status}")
            if err:
                print(f"     code={err.code} | message={err.message}")
            return False
    finally:
        client.agents.delete(agent.id)
        print(f"  Agent deleted\n")


# ── Tests ─────────────────────────────────────────────────────────────────────
results = {}

# Step 1 — bare agent, no tools, no system prompt
results["1_bare"] = run_test(
    "STEP 1 — Bare agent (no tools, no system prompt)",
    tools=None,
    instructions="You are a helpful assistant.",
    message="Say hello in one sentence.",
)

# Step 2 — function tools only
if results["1_bare"]:
    from agent.tools import check_eac_variance, list_projects_with_material_movements
    ft = FunctionTool(functions={check_eac_variance, list_projects_with_material_movements})
    results["2_tools"] = run_test(
        "STEP 2 — Function tools only",
        tools=ft.definitions,
        instructions="You help validate project narratives.",
        message="What is 2 + 2? (don't use tools)",
    )
else:
    print("STEP 2 skipped — Step 1 failed\n")
    results["2_tools"] = None

# Step 3 — real system prompt, no tools
if results["1_bare"]:
    from agent.system_prompt import get_system_prompt
    prompt = get_system_prompt()
    print(f"System prompt length: {len(prompt)} chars / ~{len(prompt)//4} tokens")
    results["3_prompt"] = run_test(
        "STEP 3 — Real system prompt, no tools",
        tools=None,
        instructions=prompt,
        message="What is your role? Reply in 2 sentences.",
    )
else:
    print("STEP 3 skipped\n")
    results["3_prompt"] = None

# Step 4 — full: system prompt + function tools
if results.get("2_tools") and results.get("3_prompt"):
    ft = FunctionTool(functions={check_eac_variance, list_projects_with_material_movements})
    results["4_full"] = run_test(
        "STEP 4 — System prompt + function tools (full config)",
        tools=ft.definitions,
        instructions=get_system_prompt(),
        message="Validate this narrative: 'The project DCA remains Amber.'",
    )
else:
    print("STEP 4 skipped\n")
    results["4_full"] = None

# ── Summary ───────────────────────────────────────────────────────────────────
print("=" * 60)
print("  SUMMARY")
print("=" * 60)
icons = {True: "✅ PASS", False: "❌ FAIL", None: "⏭  SKIP"}
for step, res in results.items():
    print(f"  {step:<20} {icons.get(res, '?')}")

fails = [k for k, v in results.items() if v is False]
if not fails:
    print("\n✅ All steps passed — the agent_runner should work.")
elif results["1_bare"] is False:
    print("\n❌ Step 1 failed — model itself can't run in Agents API.")
    print("   → Check if 'gpt-5.1-chat' deployment supports Agents API in the portal.")
    print("   → Try changing AZURE_FOUNDRY_MODEL_DEPLOYMENT to gpt-4o or gpt-4o-mini.")
elif results["2_tools"] is False:
    print("\n❌ Step 2 failed — function tool schema is invalid.")
elif results["3_prompt"] is False:
    print(f"\n❌ Step 3 failed — system prompt causes server_error ({len(get_system_prompt())} chars).")
    print("   → System prompt may be too long for this model.")
