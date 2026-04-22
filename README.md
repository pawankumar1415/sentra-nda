# NDA Narrative Validation System

AI-powered tool for validating NDA project narrative reports against formatting guidelines, EAC (Estimate at Completion) variance data, and schedule movements.

---

## Architecture Overview

The system provides **two independent validation approaches** that can be used side-by-side:

| | Custom Approach (`rag_function/`) | Foundry Approach (`agent/`) |
|---|---|---|
| **Folder** | `rag_function/` | `agent/` |
| **Function App** | `nda-python-backend` | `nda-foundry-api` |
| **AI Backend** | Azure OpenAI (GPT) + PGVector | Azure AI Foundry Agent |
| **Auth** | JWT login/register, per-user data isolation | Azure Function host key only |
| **Validation Output** | Structured JSON (scores, issues, rewrite) | Free-form agent analysis |
| **Memory** | PostgreSQL session history (per user) | Azure Blob Storage conversation blobs |
| **Search** | PGVector cosine similarity | Azure AI Search (keyword + full-text) |

```
┌─────────────────────────────────────────┐
│              React Frontend              │
│  Chat │ Validate │ Batch Validate │ Ingest │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
    ▼                     ▼
RAG Function App    Agent Function App
(nda-python-backend) (nda-foundry-api)
    │                     │
    ├─ PostgreSQL          ├─ Azure AI Foundry
    │  (PGVector)          │  (Agent + Memory Store)
    ├─ Azure OpenAI        ├─ Azure AI Search
    └─ Azure Blob          └─ Azure Blob
       (EAC data)             (EAC + conversation blobs)
```

---

## Project Structure

```
├── rag_function/                   # Custom RAG approach (Python)
│   ├── function_app.py             # All HTTP routes (auth/admin/data)
│   ├── auth.py                     # JWT authentication + user management
│   ├── db.py                       # PostgreSQL + pgvector pool + schema bootstrap
│   ├── embedder.py                 # Azure OpenAI embedding wrapper
│   ├── ingest.py                   # MPPR Excel parser + PGVector upsert (per-user)
│   ├── ingest_eac.py               # EAC variance Excel parser + upsert (per-user)
│   ├── validate.py                 # Single narrative validation pipeline
│   ├── batch_validate.py           # Batch validation pipeline
│   ├── chat.py                     # Conversational RAG pipeline (per-user)
│   ├── conversation.py             # PostgreSQL chat session + history management
│   ├── requirements.txt            # Python dependencies (includes PyJWT)
│   └── local.settings.json         # Dev credentials (gitignored)
│
├── agent/                          # Foundry approach (Python)
│   ├── function_app.py             # Azure Functions entry point
│   ├── agent_runner.py             # Foundry agent execution + blob memory
│   ├── batch_validate.py           # Batch validation for Foundry agent
│   ├── chat.py                     # Conversational chat via Foundry agent
│   ├── tools.py                    # EAC/schedule tool functions
│   ├── system_prompt.py            # Agent system prompt
│   ├── ingest_helper.py            # AI Search indexing + project search
│   ├── guidance_loader.py          # Loads Good Practice Guidelines from Blob
│   ├── config.py                   # Centralised configuration
│   ├── setup_memory.py             # Foundry Memory Store setup CLI
│   └── local.settings.json         # Dev credentials (gitignored)
│
├── frontend/                       # React + TypeScript UI (Custom approach)
│   └── src/
│       ├── context/
│       │   └── AuthContext.tsx     # JWT state (token/username/is_admin) in localStorage
│       ├── components/
│       │   ├── Sidebar.tsx         # Nav + logged-in user + logout + admin link
│       │   └── ProtectedRoute.tsx  # Redirects to /login if unauthenticated
│       ├── views/
│       │   ├── LoginView.tsx       # Sign in / Register (tab switcher)
│       │   ├── AdminView.tsx       # User management table (admin only)
│       │   ├── ChatView.tsx        # Conversational RAG assistant
│       │   ├── ValidateView.tsx    # Single narrative validation + project search
│       │   ├── BatchValidateView.tsx # Batch validation (RAG or Agent mode)
│       │   └── IngestView.tsx      # Data upload (MPPR + EAC)
│       └── services/
│           └── api.ts              # All API calls with JWT auth header
│
├── test_remote.py               # Remote smoke test for custom validate endpoint
├── test_deployment.py           # Remote smoke test for Foundry validate endpoint
├── test_local.py                # Local unit tests
├── test_rag.py                  # End-to-end RAG pipeline test
├── test_e2e.py                  # End-to-end test suite
├── test_agent.py                # Agent integration tests
├── debug_agent_run.py           # Debug script for Foundry agent execution
├── debug_agent_permissions.py   # RBAC/permissions check for Foundry agent
├── setup_db.py                  # One-time PostgreSQL schema setup script
├── ingest_files.py              # Bulk local ingest utility
├── clean_mppr_data.py           # MPPR data cleaning utility
└── README.md
```

---

## API Endpoints

### RAG Function App (`nda-python-backend`)

All data routes require `Authorization: Bearer <token>`. Admin routes additionally require an admin account.

**Auth (public)**

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/auth/register` | Create account — first user is auto-admin |
| POST | `/api/auth/login` | Returns JWT token (8-hour expiry) |

**Admin (admin JWT required)**

| Method | Route | Description |
|--------|-------|-------------|
| GET  | `/api/mgmt/users` | List all users with stats |
| POST | `/api/mgmt/users/update` | Toggle `is_active` or `is_admin` flag |
| POST | `/api/mgmt/users/delete` | Delete user and all their data |

**Data (auth JWT required)**

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/ingest` | Upload MPPR Excel → embed → store in PGVector (per-user) |
| POST | `/api/ingest-eac` | Upload EAC variance Excel → store in PostgreSQL (per-user) |
| POST | `/api/validate` | Validate single narrative (structured JSON result) |
| POST | `/api/chat` | Conversational RAG with PostgreSQL session memory (per-user) |
| POST | `/api/list-projects` | Extract project list from Excel (no DB write) |
| POST | `/api/batch-validate` | Validate all narratives in an Excel file |
| GET  | `/api/search-projects?q=<term>` | Search user's indexed projects by name |

### Agent Function App (`nda-foundry-api`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/ingest-mppr` | Upload MPPR Excel → index in Azure AI Search |
| POST | `/api/ingest-eac` | Upload EAC variance Excel → Azure Blob Storage |
| POST | `/api/validate` | Validate narrative using AI Foundry agent |
| POST | `/api/batch-validate` | Batch validate all narratives in an Excel file |
| POST | `/api/chat` | Conversational QA over indexed project data |
| GET  | `/api/search-projects?q=<term>` | Wildcard search of indexed project names |

---

## Conversation Memory

### RAG Function

**Short-term (PostgreSQL session memory)**
- First `/api/chat` call creates a session UUID scoped to the logged-in user
- Client stores UUID in `localStorage` and sends it on subsequent calls
- PostgreSQL `chat_sessions` + `chat_messages` tables store full history
- Last 20 messages are injected per LLM call; full history retained for audit

### Agent Function

**Short-term (Azure Blob Storage)**
- First `/api/validate` or `/api/chat` call generates a UUID conversation ID
- Conversation history stored as a JSON blob: `nda-data/conversations/<uuid>.json`
- Client passes `conversation_id` on follow-up calls to resume context within the same session
- Non-fatal: if the blob write fails, validation still completes

**Long-term (Azure AI Foundry Memory Store)**
- Extracts and consolidates key facts across sessions — projects validated, recurring issues, user preferences
- Recalled automatically via `agent_reference` on every subsequent call; no extra code needed in the caller
- Scoped per user via the `user_scope` parameter so different users do not share memories
- Requires an embedding model deployment alongside the chat model
- Setup: run `python agent/setup_memory.py create` once, or add via the Foundry portal under Memory (Preview)

---

## Frontend Features

### Login / Register (`/login`)
- Public page — only entry point without a token
- Tab switcher between Sign In and Register
- First registered account is automatically admin
- Token stored in `localStorage`, survives page refresh

### Chat (`/chat`)
- Conversational interface backed by the RAG function
- Session history persisted in PostgreSQL (scoped to logged-in user)
- "New Conversation" button to clear history

### Individual Narrative Validation (`/validate`)
- Upload MPPR Excel to auto-populate project list
- **Wild search**: type in the project name field to search projects already indexed in PGVector — no Excel upload needed
- Structured validation result: compliance score, Layer 1 (format) + Layer 2 (data) issues
- AI rewritten narrative

### Batch Validation (`/batch-validate`)
- Upload MPPR Excel to validate all projects at once
- **Engine toggle**: switch between RAG (structured output) and AI Foundry Agent (deep narrative analysis)
- Live progress bar showing current project
- Export results to Excel

### Data Ingest (`/ingest`)
- Upload MPPR Excel to index into PGVector (data scoped to your account)
- Upload EAC variance Excel to update your financial reference data

### Admin Panel (`/admin` — admin only)
- User management table: all registered users with project and session counts
- Toggle active/inactive status per user
- Toggle admin rights per user
- Delete user and all associated data

---

## Setup

### Prerequisites
- Azure subscription with:
  - Azure Database for PostgreSQL Flexible Server (with pgvector extension)
  - Azure OpenAI resource (embeddings + chat deployments)
  - Azure AI Foundry project + agent (`nda-narrative-validator-v3`)
  - Azure AI Search instance
  - Azure Blob Storage account

### Local Development

**RAG function:**
```bash
cd rag_function
# Fill in local.settings.json with real values including JWT_SECRET
pip install -r requirements.txt
func start
```

**Agent function:**
```bash
cd agent
# Fill in local.settings.json with real values
python setup_memory.py list   # verify Memory Store
func start
```

**Frontend:**
```bash
cd frontend
npm install
# Create frontend/src/local.settings.json with AZURE_FUNCTION_KEY
npm run dev
# Opens at http://localhost:5173 → redirects to /login
# Register the first account → it becomes admin automatically
```

### Deployment

**RAG function:**
```bash
cd rag_function
func azure functionapp publish nda-python-backend --python
```

**Agent function:**
```bash
cd agent
func azure functionapp publish nda-foundry-api --python
```

**Frontend:** Build and deploy to Azure Static Web Apps or your preferred host.
```bash
cd frontend && npm run build
```

---

## Configuration

Both function apps read all settings from environment variables. In local dev these come from `local.settings.json` (gitignored). In Azure they come from Function App Settings.

### Key settings — RAG function

| Variable | Description |
|----------|-------------|
| `POSTGRES_HOST` | PostgreSQL server hostname |
| `POSTGRES_DB` | Database name |
| `POSTGRES_USER` | DB user (Entra ID email or username) |
| `POSTGRES_PASSWORD` | DB password (leave empty for Managed Identity) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment name |
| `AZURE_OPENAI_EMBEDDING_DIMS` | Embedding dimensions (e.g. `3072` for text-embedding-3-large) |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Chat model deployment name |
| `JWT_SECRET` | Random secret for signing JWT tokens — **required for auth to work** |

### Key settings — Agent function

| Variable | Description |
|----------|-------------|
| `AZURE_FOUNDRY_PROJECT_ENDPOINT` | AI Foundry project endpoint URL |
| `AZURE_FOUNDRY_MODEL_DEPLOYMENT` | Chat model deployment name |
| `AZURE_AI_SEARCH_CONNECTION_NAME` | AI Search connection name in Foundry |
| `AZURE_SEARCH_INDEX_NAME` | AI Search index name |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob storage URL (EAC + conversation blobs) |
| `MEMORY_STORE_NAME` | Foundry Memory Store name |
| `AZURE_FOUNDRY_EMBEDDING_DEPLOYMENT` | Embedding deployment for Memory Store |

---

## Testing

```bash
# RAG memory tests (unit + remote)
python test_conversation_memory.py --unit
python test_conversation_memory.py --remote

# Agent memory tests (unit + remote)
python test_agent_memory.py --unit
python test_agent_memory.py --remote
```

Settings are automatically loaded from `agent/local.settings.json` and `rag_function/local.settings.json`.

---

## Data Format

The system expects the NDA Executive Project Summary Excel file with a sheet named `5a)NDA MPPR`. Each project spans 2–3 rows:
- **Data row**: project name (col 1), DCA RAG status (col 3), numeric EAC/schedule data
- **Narrative row**: project name (col 0), narrative text (col 1)
- **Blank separator row**

EAC variance thresholds (agreed with stakeholders):
- `< £50k` — no comment needed
- `£50k–£100k` — monitoring flag
- `≥ £100k` — must appear in narrative
- `≥ £500k` — requires explicit explanation