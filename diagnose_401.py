"""
diagnose_401.py — Pinpoints exactly which Azure permission is causing the
Responses API 401 error.

Run:
    venv\Scripts\python diagnose_401.py

Reads credentials from agent/local.settings.json automatically.
Add AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP to local.settings.json
to also get a role-assignment dump.
"""

from __future__ import annotations
import json, os, pathlib, sys, time
import requests as _requests

# ── Load agent/local.settings.json ────────────────────────────────────────────
settings_path = pathlib.Path("agent/local.settings.json")
if settings_path.exists():
    for k, v in json.load(open(settings_path)).get("Values", {}).items():
        os.environ.setdefault(k, v)
    print(f"✅ Loaded settings from {settings_path}\n")
else:
    sys.exit(f"❌  {settings_path} not found — cannot run without credentials")

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.ai.projects import AIProjectClient

# ── Credential ────────────────────────────────────────────────────────────────
tenant_id     = os.environ.get("AZURE_TENANT_ID", "")
client_id     = os.environ.get("AZURE_CLIENT_ID", "")
client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
endpoint      = os.environ.get("AZURE_FOUNDRY_PROJECT_ENDPOINT", "")
model         = os.environ.get("AZURE_FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o")

USE_SP = bool(tenant_id and client_id and client_secret and "<" not in client_id)
if USE_SP:
    cred = ClientSecretCredential(tenant_id, client_id, client_secret)
    print(f"Auth : Service Principal  (client_id={client_id})")
else:
    cred = DefaultAzureCredential()
    print("Auth : DefaultAzureCredential")

print(f"Endpoint : {endpoint}")
print(f"Model    : {model}")
print("─" * 60)

results: dict = {}
openai_client = None  # set in Test 2


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_ROLES = {
    "5e0bd9bd7196d9e99b895e12": "Cognitive Services OpenAI User",
    "a001fd3d-188f-4b5d-821b": "Cognitive Services OpenAI Contributor",
    "25fbc0a9-bd7c-42a3-aa1a": "Cognitive Services User",
    "64702f94-c441-49e6-a78b": "Azure AI User",
    "de6d5322-2949-4740-82b2": "Azure AI Developer",
    "f6c7c914-8db3-469d-8ca1": "Azure AI Inference Deployment Operator",
    "b24988ac-6180-42a0-ab88": "Contributor",
    "8311e382-0749-4cb8-b61a": "Storage Blob Data Contributor",
}

def role_name(role_def_id: str) -> str:
    guid = role_def_id.split("/")[-1]
    # check prefix match for the partial GUIDs in KNOWN_ROLES
    for partial, name in KNOWN_ROLES.items():
        if guid.startswith(partial):
            return name
    return guid


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Token acquisition
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] Token acquisition")

for scope_label, scope in [
    ("AI Foundry  ", "https://ai.azure.com/.default"),
    ("CogServices ", "https://cognitiveservices.azure.com/.default"),
    ("Management  ", "https://management.azure.com/.default"),
]:
    try:
        tok = cred.get_token(scope)
        print(f"  ✅ {scope_label} token OK (exp={tok.expires_on})")
        results[f"token_{scope_label.strip()}"] = tok
    except Exception as e:
        print(f"  ❌ {scope_label} FAILED: {e}")
        results[f"token_{scope_label.strip()}"] = None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Build openai_client
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Build openai_client from AIProjectClient")
try:
    project = AIProjectClient(endpoint=endpoint, credential=cred)
    openai_client = project.get_openai_client()
    base_url = str(openai_client.base_url)
    print(f"  ✅ openai_client.base_url = {base_url}")
    results["openai_client"] = True
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    results["openai_client"] = False
    print("\n❌ Cannot continue — AIProjectClient failed.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Chat Completions (baseline — should work)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Chat Completions API (baseline)")
try:
    cc = openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Say the word OK only."}],
        max_tokens=10,
    )
    text = cc.choices[0].message.content
    print(f"  ✅ Worked — response: {text!r}")
    results["chat_completions"] = True
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    results["chat_completions"] = False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Responses API via SDK (the failing path)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Responses API via SDK openai_client.responses.create()")
try:
    resp = openai_client.responses.create(
        model=model,
        instructions="You are a test assistant.",
        input=[{"type": "message", "role": "user", "content": "Say the word OK only."}],
    )
    text_parts = []
    for item in resp.output:
        if getattr(item, "type", "") == "message":
            for block in getattr(item, "content", []):
                if getattr(block, "type", "") == "output_text":
                    text_parts.append(block.text)
    text = " ".join(text_parts)
    print(f"  ✅ Worked — response: {text!r}")
    results["responses_api_sdk"] = True
except Exception as e:
    err_str = str(e)
    print(f"  ❌ FAILED: {err_str[:300]}")
    # Extract HTTP status if available
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status:
        print(f"     HTTP status: {status}")
    try:
        body = e.response.json()
        print(f"     Error body : {json.dumps(body, indent=4)[:600]}")
    except Exception:
        pass
    results["responses_api_sdk"] = False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Responses API via raw HTTP with cognitiveservices token
#           (isolates whether it's a token-scope issue)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] Responses API via raw HTTP with cognitiveservices token")
cs_tok = results.get("token_CogServices")
if cs_tok and not results.get("responses_api_sdk"):
    try:
        base = base_url.rstrip("/")
        # Try both api-version strings
        for api_ver in ["2025-03-01-preview", "2024-12-01-preview"]:
            url = f"{base}/responses?api-version={api_ver}"
            r = _requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {cs_tok.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "instructions": "You are a test assistant.",
                    "input": [{"type": "message", "role": "user", "content": "Say OK."}],
                },
                timeout=30,
            )
            print(f"  api-version={api_ver} → HTTP {r.status_code}")
            if r.status_code == 200:
                print(f"  ✅ Raw HTTP call WORKED with api-version={api_ver}")
                results["responses_api_raw"] = True
                break
            else:
                snippet = r.text[:250].replace("\n", " ")
                print(f"     Response: {snippet}")
                results["responses_api_raw"] = False
    except Exception as e:
        print(f"  ❌ Exception: {e}")
        results["responses_api_raw"] = False
elif results.get("responses_api_sdk"):
    print("  ⏭  SKIPPED — SDK test already passed")
    results["responses_api_raw"] = None
else:
    print("  ⏭  SKIPPED — no CogServices token")
    results["responses_api_raw"] = None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — Responses API via raw HTTP with AI Foundry token
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] Responses API via raw HTTP with AI Foundry token")
foundry_tok = results.get("token_AI Foundry")
if foundry_tok and not results.get("responses_api_sdk"):
    try:
        base = base_url.rstrip("/")
        for api_ver in ["2025-03-01-preview", "2024-12-01-preview"]:
            url = f"{base}/responses?api-version={api_ver}"
            r = _requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {foundry_tok.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "instructions": "You are a test assistant.",
                    "input": [{"type": "message", "role": "user", "content": "Say OK."}],
                },
                timeout=30,
            )
            print(f"  api-version={api_ver} → HTTP {r.status_code}")
            if r.status_code == 200:
                print(f"  ✅ AI Foundry token WORKED — this is a token-scope issue")
                results["responses_api_foundry_token"] = True
                break
            else:
                snippet = r.text[:250].replace("\n", " ")
                print(f"     Response: {snippet}")
                results["responses_api_foundry_token"] = False
    except Exception as e:
        print(f"  ❌ Exception: {e}")
        results["responses_api_foundry_token"] = False
else:
    print("  ⏭  SKIPPED")
    results["responses_api_foundry_token"] = None


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — Role assignments (requires AZURE_SUBSCRIPTION_ID + AZURE_RESOURCE_GROUP)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] Role assignments on the AI Services resource")
sub_id     = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
rg_name    = os.environ.get("AZURE_RESOURCE_GROUP", "")
ai_account = os.environ.get("AZURE_AI_SERVICES_ACCOUNT", "movar-secure-azure-resource")
mgmt_tok   = results.get("token_Management")

if sub_id and rg_name and mgmt_tok and "<" not in sub_id:
    try:
        resource_id = (
            f"/subscriptions/{sub_id}"
            f"/resourceGroups/{rg_name}"
            f"/providers/Microsoft.CognitiveServices/accounts/{ai_account}"
        )
        r = _requests.get(
            f"https://management.azure.com{resource_id}"
            "/providers/Microsoft.Authorization/roleAssignments",
            headers={"Authorization": f"Bearer {mgmt_tok.token}"},
            params={"api-version": "2022-04-01"},
            timeout=30,
        )
        if r.status_code == 200:
            assignments = r.json().get("value", [])
            print(f"  ✅ {len(assignments)} role assignment(s) on {ai_account}:")
            for ra in assignments:
                p  = ra["properties"]
                rn = role_name(p["roleDefinitionId"])
                print(f"      principal={p['principalId']!r:40}  role={rn}")
            results["role_assignments"] = assignments
        else:
            print(f"  ❌ HTTP {r.status_code}: {r.text[:200]}")
            results["role_assignments"] = None
    except Exception as e:
        print(f"  ❌ Exception: {e}")
        results["role_assignments"] = None
else:
    print("  ⏭  SKIPPED — add AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP to")
    print("               agent/local.settings.json to enable this check")
    results["role_assignments"] = None


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY + DIAGNOSIS
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
icon = lambda v: "✅ PASS" if v is True else ("❌ FAIL" if v is False else "⏭  SKIP")
for k, v in results.items():
    if k == "role_assignments":
        continue
    display = v if isinstance(v, bool) else (v is not None)
    print(f"  {k:<35} {icon(display)}")

print("\n" + "=" * 60)
print("DIAGNOSIS")
print("=" * 60)

cc_ok   = results.get("chat_completions") is True
resp_ok = results.get("responses_api_sdk") is True
raw_cs  = results.get("responses_api_raw")
raw_fd  = results.get("responses_api_foundry_token")

if resp_ok:
    print("""
✅ Responses API is NOW WORKING.
   The 401 was likely caused by the agent_reference in the request body (which
   required agents/write).  Removing it fixed the issue.
   → Deploy: func azure functionapp publish nda-foundry-api
""")
elif cc_ok and not resp_ok:
    print("""
❌ Chat Completions WORKS but Responses API returns 401.

   Both use the same openai_client, but the Responses API requires an extra
   data-plane permission that Chat Completions does not:

     Microsoft.CognitiveServices/accounts/OpenAI/responses/action

   This permission is granted by:
     • "Cognitive Services OpenAI Contributor"  (most permissive — use this)
     • "Cognitive Services OpenAI User"         (read/inference only — try first)

   THE FIX (Azure Portal):
   ─────────────────────────────────────────────────────────────
   1. Go to: Azure Portal → All resources → movar-secure-azure-resource
              (the Cognitive Services / AI Services resource)
   2. Click: Access control (IAM) → Add role assignment
   3. Role:  Cognitive Services OpenAI Contributor  ← assign this
   4. Assign access to: Managed identity
   5. Select:  nda-foundry-api  (system-assigned managed identity)
              Object ID: af2cb0ed-c99b-4e73-9d2f-34c2db0ffe20
   6. Save → wait ~2 min for propagation → redeploy → retest
   ─────────────────────────────────────────────────────────────
""")
    if raw_cs is True:
        print("   NOTE: Raw HTTP call with CogServices token WORKED.")
        print("   This confirms it's a role-assignment gap, not a code bug.")
    elif raw_cs is False:
        print("   NOTE: Raw HTTP call with CogServices token ALSO failed.")
        print("   This confirms the managed identity is missing the role entirely.")
    if raw_fd is True:
        print("   NOTE: AI Foundry token worked — assign the Foundry token scope role instead.")

elif not cc_ok and not resp_ok:
    print("""
❌ BOTH Chat Completions AND Responses API failed with 401.

   The managed identity has NO inference permissions on the AI Services resource.
   Assign 'Cognitive Services OpenAI User' on movar-secure-azure-resource.
""")
else:
    print("   ⚠️  Unexpected result combination — check individual test outputs above.")