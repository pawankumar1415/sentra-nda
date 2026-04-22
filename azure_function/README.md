# NDA MPPR — Azure Function (Standalone Ingestion)

> **Note:** This is the original standalone ingestion function. The active deployment is the `agent/` folder (`nda-foundry-api`), which includes ingestion, validation, chat, and batch validation in a single Function App. This folder is retained as a reference implementation.

## What this does

This Azure Function exposes an HTTP POST endpoint (`/api/ingest-mppr`) that:

1. Accepts an `.xlsx` file as a `multipart/form-data` upload.
2. Parses the **`5a)NDA MPPR`** sheet, columns A → AK, extracting structured project records.
3. Pushes each record (with `mergeOrUpload`) to an **Azure AI Search** index.

---

## Folder structure

```
azure_function/
├── function_app.py          # Main code — trigger, parser, uploader
├── host.json                # Azure Functions runtime config
├── local.settings.json      # Secrets — DO NOT commit to source control
└── requirements.txt         # Python packages for the Function runtime
```

---

## Authentication

This function uses **`DefaultAzureCredential`** (Managed Identity in Azure, `az login` locally). No API keys are stored in code or configuration.

Required RBAC role on the Azure AI Search resource: **Search Index Data Contributor**

---

## Configuration

Fill in `local.settings.json` before running locally. In Azure, set these as Function App Settings.

| Setting | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint URL (e.g. `https://my-search.search.windows.net`) |
| `AZURE_SEARCH_INDEX_NAME` | Name of the target index (default: `nda-mppr-projects`) |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob Storage account URL (for EAC variance file) |
| `AZURE_STORAGE_CONTAINER_NAME` | Blob container name (default: `nda-data`) |
| `EAC_BLOB_NAME` | EAC variance blob name (default: `lifecycle_eac_variance.xlsx`) |

> **Note:** `local.settings.json` is gitignored by default. Never check it in.

---

## Azure AI Search Index Schema

The index is created automatically on first run via `ensure_index_exists()`. Fields:

| Field | Type | Attributes |
|---|---|---|
| `id` | `Edm.String` | Key |
| `ProjectName` | `Edm.String` | Searchable, Filterable, Sortable |
| `ReportingPeriod` | `Edm.String` | Filterable, Sortable, Facetable |
| `DCA_RAG_Status` | `Edm.String` | Filterable, Facetable |
| `DCA_RAG_Movement` | `Edm.Int32` | Filterable, Sortable |
| `Baseline_RAG_Status` | `Edm.String` | Filterable, Facetable |
| `Capability_Capacity_RAG` | `Edm.String` | Filterable, Facetable |
| `BusinessCase_Cost_P50` | `Edm.Double` | Filterable, Sortable |
| `BusinessCase_Cost_P80` | `Edm.Double` | Filterable, Sortable |
| `EAC_Cost` | `Edm.Double` | Filterable, Sortable |
| `EAC_vs_Last_Period` | `Edm.Double` | Filterable, Sortable |
| `Baseline_Cost_P50` | `Edm.Double` | Filterable, Sortable |
| `Timeline_Delay_Days` | `Edm.Int32` | Filterable, Sortable |
| `CPI` | `Edm.Double` | Filterable |
| `SPI` | `Edm.Double` | Filterable |
| `Pct_Complete` | `Edm.Double` | Filterable |
| `NarrativeText` | `Edm.String` | Searchable (en.microsoft analyser) |

---

## How to call the endpoint

### cURL
```bash
curl -X POST "http://localhost:7071/api/ingest-mppr" \
  -F "file=@P07 Exec Project Summary FINAL.xlsx" \
  -F "reporting_period=P07"
```

### Python requests
```python
import requests

with open("P07 Exec Project Summary FINAL.xlsx", "rb") as f:
    resp = requests.post(
        "http://localhost:7071/api/ingest-mppr",
        files={"file": f},
        data={"reporting_period": "P07"},
    )
print(resp.json())
```

### Expected response
```json
{
  "message": "Processing complete.",
  "reporting_period": "P07",
  "projects_extracted": 14,
  "documents_indexed": 14,
  "documents_failed": 0
}
```

---

## Running locally

```bash
cd azure_function
pip install -r requirements.txt
func start
```