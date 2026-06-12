# Power Platform — Foundry Approach

The Foundry approach is accessed through a Power Apps Canvas App and 12 Power Automate flows. There is no web frontend — users interact entirely through Power Apps.

---

## Canvas App

**Name:** NDA Narrative Validator (Canvas App)
**Platform:** Power Apps (Canvas)

The Canvas App is the user-facing interface. It collects input from the user (file uploads, project selection, narrative text) and triggers the appropriate Power Automate flow. It does not call the Function App directly — all Function App calls are made by flows.

The Canvas App and all 12 flows are bundled in a single Power Platform solution: **NDA Narrative Power Platform**.

---

## Connection to the Function App

All flows call `https://nda-foundry-api.azurewebsites.net/api/<route>` using the Azure Function host key.

The host key is stored as a connection variable inside each flow — not hardcoded in the Canvas App. Rotating the key requires updating the connection in Power Automate.

---

## All 12 Flows

### Data Ingest Flows

| Flow | Trigger | Function App Route | What it does |
|---|---|---|---|
| **IngestMPPRFlow** | SharePoint file → Canvas App | `POST /api/pgvector/ingest-mppr` | Parses MPPR Excel, embeds narratives, upserts into nda_projects |
| **IngestMPPRLocalFlow** | Direct file upload in Canvas App | `POST /api/pgvector/ingest-mppr` | Same as above but file sourced from local device |
| **IngestEACFlow** | SharePoint file → Canvas App | `POST /api/pgvector/ingest-eac` | Uploads EAC variance Excel to Azure Blob |
| **IngestEACLocalFlow** | Direct file upload in Canvas App | `POST /api/pgvector/ingest-eac` | Same as above but file sourced from local device |

---

### Validation Flows

| Flow | Trigger | Function App Route | What it does |
|---|---|---|---|
| **ValidateNarrativeFlow** | User selects project, clicks Validate (SharePoint source) | `POST /api/pgvector/validate` | Single narrative validation — pgvector context + Foundry agent |
| **ValidateNarrativeLocalFlow** | User selects project, clicks Validate (local file source) | `POST /api/pgvector/validate` | Same as above but project sourced from local file |
| **BatchValidateFlow** | User uploads MPPR, clicks Batch Validate (SharePoint) | `POST /api/pa-batch-validate` | Validates all project narratives in one Excel file |
| **BatchValidateLocalFlow** | User uploads MPPR, clicks Batch Validate (local file) | `POST /api/pa-batch-validate` | Same as above but file sourced from local device |

---

### Chat Flows

| Flow | Trigger | Function App Route | What it does |
|---|---|---|---|
| **ChatWithNDADataFlow** | User sends chat message (SharePoint project context) | `POST /api/pgvector/chat` | Conversational assistant with pgvector retrieval |
| **ChatWithNDADataLocalFlow** | User sends chat message (local project context) | `POST /api/pgvector/chat` | Same as above |

---

### SharePoint Utility Flows

| Flow | Trigger | Function App Route | What it does |
|---|---|---|---|
| **ListSPFilesFlow** | User opens file picker | **SharePoint connector only** — no Function App call | Lists Excel files in SentraFileStaging library |
| **ListProjectsFromSPFlow** | User selects a SharePoint file to populate dropdown | `POST /api/pgvector/list-projects` | Parses Excel and returns project names — no DB write |

---

## Local vs SharePoint Variants

Each main operation (ingest, validate, batch, chat) has two flow variants:

- **SharePoint variant** — user picks a file from the SentraFileStaging SharePoint library. The flow downloads the file via the SharePoint connector and forwards the bytes to the Function App.
- **Local variant** — user uploads a file directly from their device in the Canvas App. The flow forwards the raw bytes to the Function App.

Both variants call the same Function App route. The route behaves identically regardless of where the file came from.

---

## Flow That Bypasses the Function App

**ListSPFilesFlow** and **ExportBatchResultsFlow** do not call the Function App at all:

- **ListSPFilesFlow** — uses the Power Platform SharePoint connector to list files in the SentraFileStaging library and return them to the Canvas App for the file picker dropdown.
- **ExportBatchResultsFlow** — takes the batch results already held in Canvas App memory and writes them to a SharePoint list or Excel file using the SharePoint connector.

---

## Authentication

No per-user authentication in the Foundry approach. Access is controlled at the Power Apps / Power Automate layer:

- Canvas App is shared with users within the organisation's Microsoft 365 tenant.
- Flows call the Function App using a shared host key embedded in the flow connections.
- The Function App uses Managed Identity to authenticate to PostgreSQL, Azure Blob, and Azure AI Services — no connection strings needed.

---

## Solution Export / Import

The entire Power Platform solution (Canvas App + all 12 flows) can be exported as a `.zip` from the Power Apps maker portal and imported to a new environment. After import, update the Function App host key in the flow connections.