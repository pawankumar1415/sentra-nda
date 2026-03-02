"""
create_index.py  —  One-time setup script to create the Azure AI Search index.

Run this ONCE before deploying / testing the Azure Function:

    python create_index.py

It reads connection details from azure_function/local.settings.json so you do not
need to export any environment variables manually.
"""

import json
import pathlib
import sys

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
)


# ── Read credentials from local.settings.json ─────────────────────────────────
settings_path = pathlib.Path(__file__).parent / "azure_function" / "local.settings.json"

if not settings_path.exists():
    print(f"ERROR: Cannot find {settings_path}")
    sys.exit(1)

with open(settings_path, encoding="utf-8") as f:
    settings = json.load(f)

values = settings.get("Values", {})
ENDPOINT   = values.get("AZURE_SEARCH_ENDPOINT")
API_KEY    = values.get("AZURE_SEARCH_API_KEY")
INDEX_NAME = values.get("AZURE_SEARCH_INDEX_NAME", "nda-mppr-projects")

if not ENDPOINT or not API_KEY:
    print("ERROR: AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY missing in local.settings.json")
    sys.exit(1)


# ── Define the index schema ───────────────────────────────────────────────────
fields = [
    # ─ Primary key ─
    SimpleField(
        name="id",
        type=SearchFieldDataType.String,
        key=True,
        filterable=True,
    ),

    # ─ Identifiers ─
    SearchableField(
        name="ProjectName",
        type=SearchFieldDataType.String,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="ReportingPeriod",
        type=SearchFieldDataType.String,
        filterable=True,
        sortable=True,
        facetable=True,
    ),

    # ─ RAG statuses ─
    SimpleField(
        name="DCA_RAG_Status",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="DCA_RAG_Movement",
        type=SearchFieldDataType.Int32,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="Baseline_RAG_Status",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="Capability_Capacity_RAG",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),

    # ─ Financial metrics ─
    SimpleField(
        name="BusinessCase_Cost_P50",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="BusinessCase_Cost_P80",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="EAC_Cost",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="EAC_vs_Last_Period",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="Baseline_Cost_P50",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),

    # ─ Schedule metrics ─
    SimpleField(
        name="Timeline_Delay_Days",
        type=SearchFieldDataType.Int32,
        filterable=True,
        sortable=True,
    ),

    # ─ Performance indices ─
    SimpleField(
        name="CPI",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="SPI",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="Pct_Complete",
        type=SearchFieldDataType.Double,
        filterable=True,
        sortable=True,
    ),

    # ─ Narrative (full-text searchable) ─
    SearchableField(
        name="NarrativeText",
        type=SearchFieldDataType.String,
        analyzer_name="en.microsoft",  # English-optimised analyser
    ),
]

index = SearchIndex(name=INDEX_NAME, fields=fields)


# ── Create or update the index ────────────────────────────────────────────────
if __name__ == "__main__":
    client = SearchIndexClient(
        endpoint=ENDPOINT,
        credential=AzureKeyCredential(API_KEY),
    )

    try:
        existing = client.get_index(INDEX_NAME)
        print(f"Index '{INDEX_NAME}' already exists. Updating schema if needed...")
        result = client.create_or_update_index(index)
        print(f"Index '{result.name}' updated successfully.")
    except Exception:
        print(f"Creating new index '{INDEX_NAME}'...")
        result = client.create_index(index)
        print(f"Index '{result.name}' created successfully with {len(result.fields)} fields.")
