"""
test_agent.py — Quick local test for the AI Agent (all three modes).

Run this from the repo root AFTER filling in agent/local.settings.json:

    venv\Scripts\python test_agent.py

No Teams / Bot Service required — connects directly to Azure AI Foundry.
"""

import json
import os
import pathlib

# ── Load credentials from agent/local.settings.json ──────────────────────────
settings_path = pathlib.Path(__file__).parent / "agent" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for key, value in json.load(f).get("Values", {}).items():
            os.environ.setdefault(key, value)
    print(f"✅ Loaded credentials from {settings_path}\n")
else:
    print(f"⚠️  {settings_path} not found — relying on existing env vars\n")

# ── Now import (after env vars are set) ───────────────────────────────────────
from agent.agent_runner import validate_narrative, batch_validate, generate_narrative

# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Layer 2 tool only (no agent call, just the EAC variance lookup)
# This tests that lifecycle_eac_variance.xlsx is found and readable.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("TEST 1 — EAC Variance Tool (no agent call)")
print("=" * 60)
from agent.tools import check_eac_variance, list_projects_with_material_movements

result = json.loads(check_eac_variance("Sellafield Product"))
print(f"Project   : {result.get('project_name')}")
print(f"Period    : {result.get('period')}")
print(f"EAC Var   : £{result.get('eac_variance_gbp', 0)/1_000_000:.2f}m")
print(f"Assessment: {result.get('eac_assessment', {}).get('label')}")
print(f"Sched Var : {result.get('schedule_variance_days')} days")
print(f"\n{result.get('overall_narrative_requirement')}\n")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Batch listing (no agent call, just the tool)
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("TEST 2 — Projects with Material Movements (no agent call)")
print("=" * 60)
batch = json.loads(list_projects_with_material_movements())
print(f"Total flagged: {batch['total_flagged']}")
for p in batch["projects"][:5]:
    print(f"  • {p['project_name']}: {p['summary']}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Full agent: validate_narrative (NDA-01)
# Uses a real project narrative from the P07 workbook.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("TEST 3 — validate_narrative (NDA-01) — Agent call")
print("=" * 60)

SAMPLE_NARRATIVE = """
The SSAU project will provide infrastructure and equipment that will provide security and emergency response 
teams with a significantly improved capability to react to and manage security events on the Sellafield site. 
The DCA remains stable at Green. The Baseline RAG has remained Red as the cost forecast remains beyond 
the P50 baseline. The Lifecycle EAC has increased slightly but remains within P80 Sanction. 
Capability and Capacity RAG is Amber as the project team is now a thin layer and any loss of resources 
would impact delivery.
"""

try:
    result = validate_narrative(
        project_name="Security Systems Architecture Upgrade (SSAU)",
        narrative_text=SAMPLE_NARRATIVE,
        period="P07",
    )
    print(result["validation_result"])
    print(f"\n[Thread ID for follow-up: {result['thread_id']}]")
except Exception as e:
    print(f"❌ Agent call failed: {e}")
    print("   Check: Is AZURE_FOUNDRY_PROJECT_ENDPOINT set correctly in agent/local.settings.json?")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Batch validation report (NDA-002) — Agent call
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 4 — batch_validate (NDA-002) — Agent call")
print("=" * 60)

try:
    batch_result = batch_validate("2025-P11")
    print(batch_result["exceptions_report"])
except Exception as e:
    print(f"❌ Batch agent call failed: {e}")
