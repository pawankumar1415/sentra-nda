# NDA MPPR — Azure Function

## What this does
This Azure Function exposes a single **HTTP POST** endpoint (`/api/process_mppr`) that:

1. Accepts an `.xlsx` file (like P07) as a `multipart/form-data` upload.
2. Parses the **`5a)NDA MPPR`** sheet, columns A → AK, extracting structured project records.
3. Pushes each record (with `mergeOrUpload`) to an **Azure AI Search** index so Foundry Agents can query it.

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

## Configuration — what I need from you

Before running locally or deploying, fill in three values in **`local.settings.json`**:

| Setting | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | Your Azure AI Search endpoint URL (e.g. `https://my-search.search.windows.net`) |
| `AZURE_SEARCH_API_KEY` | An **Admin** API key for the search service |
| `AZURE_SEARCH_INDEX_NAME` | Name of the target index (default suggestion: `nda-mppr-projects`) |

> **Note:** `local.settings.json` is gitignored by the Azure Functions tooling by default. Never check it in.

When deployed to Azure, these same keys should be added as **Application Settings** on the Function App resource.

---

## Azure AI Search Index Schema

You (or a one-time setup script) need to create the index with the following fields before the function runs:

| Field | Type | Attributes |
|---|---|---|
| `id` | `Edm.String` | **Key** |
| `ProjectName` | `Edm.String` | Searchable, Filterable |
| `ReportingPeriod` | `Edm.String` | Filterable, Sortable |
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
| `NarrativeText` | `Edm.String` | Searchable |

---

## How to call the endpoint

### cURL
```bash
curl -X POST "http://localhost:7071/api/process_mppr" \
  -F "file=@P07 Exec Project Summary FINAL.xlsx" \
  -F "reporting_period=P07"
```

### Python requests
```python
import requests

with open("P07 Exec Project Summary FINAL.xlsx", "rb") as f:
    resp = requests.post(
        "http://localhost:7071/api/process_mppr",
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
