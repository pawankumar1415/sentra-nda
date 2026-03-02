"""
test_local.py  —  End-to-end local test for the Azure Function.

Run this while `func start` is running in azure_function/:

    python test_local.py

What it does:
    1. Calls the local Azure Function endpoint with the real P07 Excel file.
    2. Prints the JSON response.
    3. Then queries Azure AI Search directly and prints how many documents
       are now in the index for reporting period P07.

Requirements:
    pip install requests azure-search-documents
"""

import json
import pathlib

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

# ── Config ─────────────────────────────────────────────────────────────────────
FUNCTION_URL = "http://localhost:7071/api/process_mppr"

settings_path = pathlib.Path(__file__).parent / "azure_function" / "local.settings.json"
with open(settings_path, encoding="utf-8") as f:
    values = json.load(f)["Values"]

ENDPOINT   = values["AZURE_SEARCH_ENDPOINT"]
API_KEY    = values["AZURE_SEARCH_API_KEY"]
INDEX_NAME = values["AZURE_SEARCH_INDEX_NAME"]

EXCEL_FILE = (
    pathlib.Path(__file__).parent
    / "NDA Data"
    / "P07 Exec Project Summary FINAL.xlsx"
)

REPORTING_PERIOD = "P07"


# ── Step 1: Call the Azure Function ───────────────────────────────────────────
print("=" * 60)
print("STEP 1 — Calling Azure Function")
print("=" * 60)

if not EXCEL_FILE.exists():
    print(f"ERROR: Excel file not found at {EXCEL_FILE}")
    raise SystemExit(1)

with open(EXCEL_FILE, "rb") as f:
    response = requests.post(
        FUNCTION_URL,
        files={"file": (EXCEL_FILE.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"reporting_period": REPORTING_PERIOD},
        timeout=60,
    )

print(f"HTTP Status : {response.status_code}")
print("Response    :")
print(json.dumps(response.json(), indent=2))


# ── Step 2: Verify documents are searchable in Azure AI Search ────────────────
print()
print("=" * 60)
print("STEP 2 — Querying Azure AI Search to verify data")
print("=" * 60)

search_client = SearchClient(
    endpoint=ENDPOINT,
    index_name=INDEX_NAME,
    credential=AzureKeyCredential(API_KEY),
)

# Count documents for this reporting period
results = search_client.search(
    search_text="*",
    filter=f"ReportingPeriod eq '{REPORTING_PERIOD}'",
    select=["id", "ProjectName", "DCA_RAG_Status", "Capability_Capacity_RAG", "EAC_Cost"],
    top=50,
    include_total_count=True,
)

print(f"\nTotal documents in index for {REPORTING_PERIOD}: {results.get_count()}")
print()
print(f"{'Project Name':<55} {'DCA':>5} {'Cap':>5} {'EAC (£m)':>10}")
print("-" * 80)
for doc in results:
    eac = doc.get("EAC_Cost")
    eac_str = f"{eac:.1f}" if eac is not None else "N/A"
    print(
        f"{doc['ProjectName']:<55} "
        f"{(doc.get('DCA_RAG_Status') or '?'):>5} "
        f"{(doc.get('Capability_Capacity_RAG') or '?'):>5} "
        f"{eac_str:>10}"
    )

# ── Step 3: Quick semantic search test ────────────────────────────────────────
print()
print("=" * 60)
print("STEP 3 — Keyword search test: 'strike action'")
print("=" * 60)

keyword_results = search_client.search(
    search_text="strike action",
    filter=f"ReportingPeriod eq '{REPORTING_PERIOD}'",
    select=["ProjectName", "NarrativeText"],
    top=3,
)

for r in keyword_results:
    snippet = r["NarrativeText"][:200].replace("\n", " ")
    print(f"\n📌 {r['ProjectName']}")
    print(f"   {snippet}…")
