# API Endpoints

---

## RAG Function App — `nda-python-backend`

Base URL: `https://nda-python-backend.azurewebsites.net`

All data routes require `Authorization: Bearer <token>`. Admin routes additionally require an admin account. The token is obtained from `/api/auth/login` or `/api/auth/register`.

### Auth — public

| Method | Route | Description |
|---|---|---|
| POST | `/api/auth/register` | Create account. First user is auto-admin. Returns JWT token. |
| POST | `/api/auth/login` | Authenticate. Returns JWT token (8-hour expiry). |

### Admin — admin JWT required

| Method | Route | Description |
|---|---|---|
| GET | `/api/mgmt/users` | List all users with project and session counts. |
| POST | `/api/mgmt/users/update` | Toggle `is_active` or `is_admin` flag. Admin cannot modify their own account. |
| POST | `/api/mgmt/users/delete` | Delete user and all their data (projects, EAC, chat history). |

### Data — auth JWT required

| Method | Route | Description |
|---|---|---|
| POST | `/api/ingest` | Upload MPPR Excel → parse → embed → upsert into pgvector. |
| POST | `/api/ingest-eac` | Upload EAC variance Excel → parse → store in `nda_eac_variance`. |
| POST | `/api/validate` | Validate a single narrative. Returns structured JSON: compliance score, Layer 1 (format) issues, Layer 2 (data) issues, rewritten narrative. |
| POST | `/api/chat` | Conversational RAG. Creates a PostgreSQL session on first call; pass `session_id` on subsequent calls to resume. |
| POST | `/api/list-projects` | Parse project list from an uploaded Excel. No database write. |
| POST | `/api/batch-validate` | Validate all narratives in an uploaded Excel. Returns one result per project. |
| GET | `/api/search-projects?q=<term>` | Search indexed projects by name using pgvector similarity. |

---

## Agent Function App — `nda-foundry-api`

Base URL: `https://nda-foundry-api.azurewebsites.net`

All routes require the Azure Function host key passed as `?code=<host-key>` or via the `x-functions-key` header. No per-user authentication.

### Active routes — pgvector backed

These are the routes used by Power Automate flows via the Canvas App.

| Method | Route | Description |
|---|---|---|
| POST | `/api/pgvector/ingest-mppr` | Upload MPPR Excel → embed → upsert into pgvector. |
| POST | `/api/pgvector/ingest-eac` | Upload EAC variance Excel → store in Azure Blob Storage. |
| POST | `/api/pgvector/validate` | Validate narrative using Foundry agent with pgvector context retrieval. |
| POST | `/api/pgvector/batch-validate` | Batch validate all narratives in an uploaded Excel. |
| POST | `/api/pgvector/chat` | Conversational QA via Foundry agent. Conversation history stored as Blob JSON. |
| POST | `/api/pgvector/list-projects` | Parse project list from Excel. No database write. |
| GET | `/api/pgvector/search-projects?q=<term>` | Search indexed projects by name. |
| GET | `/api/pgvector/history` | Retrieve conversation history for a session. |
| POST | `/api/pa-batch-validate` | Power Automate batch validate. Does not follow `/pgvector/` path convention but internally calls `pa_batch_validate_pgvector()`. |

### Legacy routes — AI Search backed

These routes still exist in the code but are not used by current flows or the frontend. They are backed by Azure AI Search, not pgvector.

| Method | Route | Notes |
|---|---|---|
| POST | `/api/ingest-mppr` | Indexes into Azure AI Search. |
| POST | `/api/validate` | Uses AI Search retrieval. |
| POST | `/api/batch-validate` | Uses AI Search retrieval. |
| POST | `/api/chat` | Uses AI Search retrieval. |
| GET | `/api/search-projects?q=<term>` | Searches AI Search index. |

---

## Power Automate Flows — Foundry Approach

All 12 flows are part of the NDA Narrative solution in Power Platform. Flows that call the Agent Function App use the host key.

| Flow | Route called |
|---|---|
| IngestMPPRFlow | `POST /api/pgvector/ingest-mppr` |
| IngestEACFlow | `POST /api/pgvector/ingest-eac` |
| ValidateNarrativeFlow | `POST /api/pgvector/validate` |
| BatchValidateFlow | `POST /api/pa-batch-validate` |
| BatchValidateLocalFlow | `POST /api/pa-batch-validate` |
| Batch Automated Notification Foundry Flow | `POST /api/pa-batch-validate` |
| ChatFlow | `POST /api/pgvector/chat` |
| FoundryHistory | `GET /api/pgvector/history` |
| ListProjectsFromSPFlow | `POST /api/pgvector/list-projects` |
| ListProjectsLocalFlow | `POST /api/pgvector/list-projects` |
| ListSPFilesFlow | SharePoint connector — no Function App call |
| ExportBatchResultsFlow | SharePoint connector — no Function App call |