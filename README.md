# NDA Narrative Validation System

AI-powered tool for validating NDA project narrative reports against formatting guidelines, EAC (Estimate at Completion) variance data, and schedule movements.

---

## Architecture Overview

The system provides **two independent validation approaches** that can be used side-by-side:

| | RAG Approach | AI Foundry Agent Approach |
|---|---|---|
| **Folder** | `rag_function/` | `agent/` |
| **Function App** | `nda-python-backend` | `nda-foundry-api` |
| **AI Backend** | Azure OpenAI (GPT) + PGVector | Azure AI Foundry Agent |
| **Validation Output** | Structured JSON (scores, issues, rewrite) | Free-form agent analysis |
| **Memory** | PostgreSQL session history | Azure Blob Storage conversation blobs |
| **Search** | PGVector semantic search | Azure AI Search |
| **Long-term Memory** | — | Foundry Memory Store (Preview) |

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
├── rag_function/           # Custom Python RAG approach
│   ├── function_app.py     # Azure Functions entry point
│   ├── db.py               # PostgreSQL + pgvector connection
│   ├── ingest.py           # MPPR Excel parser + embedder
│   ├── validate.py         # Single narrative validation
│   ├── batch_validate.py   # Batch validation pipeline
│   ├── chat.py             # Conversational RAG with session memory
│   ├── conversation.py     # PostgreSQL session management
│   ├── embedder.py         # Azure OpenAI embedding wrapper
│   └── local.settings.json # Dev config (gitignored)
│
├── agent/                  # Azure AI Foundry Agent approach
│   ├── function_app.py     # Azure Functions entry point
│   ├── agent_runner.py     # Foundry agent execution + blob memory
│   ├── batch_validate.py   # Batch validation for Foundry agent
│   ├── tools.py            # EAC/schedule tool functions
│   ├── system_prompt.py    # Agent system prompt
│   ├── ingest_helper.py    # AI Search indexing
│   ├── config.py           # Centralised configuration
│   ├── setup_memory.py     # Foundry Memory Store setup CLI
│   └── local.settings.json # Dev config (gitignored)
│
├── frontend/               # React + TypeScript UI
│   └── src/
│       ├── views/
│       │   ├── ChatView.tsx          # Conversational RAG assistant
│       │   ├── ValidateView.tsx      # Single narrative validation + project search
│       │   ├── BatchValidateView.tsx # Batch validation (RAG or Agent mode)
│       │   └── IngestView.tsx        # Data upload (MPPR + EAC)
│       └── services/
│           └── api.ts                # API client for both backends
│
├── test_conversation_memory.py  # RAG memory integration tests
├── test_agent_memory.py         # Agent memory unit + remote tests
└── README.md
```

---

## API Endpoints

### RAG Function App (`nda-python-backend`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/ingest` | Upload MPPR Excel → embed → store in PGVector |
| POST | `/api/ingest-eac` | Upload EAC variance Excel → Azure Blob Storage |
| POST | `/api/validate` | Validate single narrative (structured JSON result) |
| POST | `/api/chat` | Conversational RAG with PostgreSQL session memory |
| POST | `/api/list-projects` | Extract project list from Excel (no DB write) |
| POST | `/api/batch-validate` | Validate all narratives in an Excel file |
| GET  | `/api/search-projects?q=<term>` | Search indexed projects by name |

### Agent Function App (`nda-foundry-api`)

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/ingest-mppr` | Upload MPPR Excel → index in Azure AI Search |
| POST | `/api/ingest-eac` | Upload EAC variance Excel → Azure Blob Storage |
| POST | `/api/validate` | Validate narrative using AI Foundry agent |
| POST | `/api/batch-validate` | Batch validate all narratives in an Excel file |

---

## Conversation Memory

### RAG Function — PostgreSQL Session Memory
- First `/api/chat` call creates a session UUID (returned to client)
- Client stores UUID in `localStorage` and sends it on subsequent calls
- PostgreSQL `chat_sessions` + `chat_messages` tables store history
- Last 20 messages are loaded per call

### Agent Function — Azure Blob Storage Conversation Memory
- First `/api/validate` call generates a UUID conversation ID
- Conversation history stored as JSON blob: `nda-data/conversations/<uuid>.json`
- Client passes `conversation_id` on follow-up calls to resume context
- Non-fatal: if blob write fails, validation still completes

### Agent Function — Foundry Memory Store (Long-term)
- Azure AI Foundry Memory Store extracts facts across sessions
- Recalled via `agent_reference` on every validate call
- Scoped per user via `user_scope` parameter
- Setup: run `python agent/setup_memory.py create` once

---

## Frontend Features

### Chat (`/chat`)
- Conversational interface backed by the RAG function
- Session persistence via `localStorage` (survives page refresh)
- "New Conversation" button to clear history

### Individual Narrative Validation (`/validate`)
- Upload MPPR Excel to auto-populate project list
- **Wild search**: type in the project name field to search projects already indexed in PGVector — no Excel upload needed
- Structured validation result: compliance score, Layer 1 (format) + Layer 2 (data) issues
- AI rewritten narrative with diff view

### Batch Validation (`/batch-validate`)
- Upload MPPR Excel to validate all projects at once
- **Engine toggle**: switch between RAG (structured output) and AI Foundry Agent (deep narrative analysis)
- Live progress bar showing current project
- Export results to Excel

### Data Ingest (`/ingest`)
- Upload MPPR Excel to index into both PGVector and AI Search
- Upload EAC variance Excel to update financial reference data

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
cp local.settings.json.example local.settings.json  # fill in real values
pip install -r requirements.txt
func start
```

**Agent function:**
```bash
cd agent
cp local.settings.json.example local.settings.json  # fill in real values
python setup_memory.py list                          # verify Memory Store
func start
```

**Frontend:**
```bash
cd frontend
npm install
# Create frontend/src/local.settings.json with AZURE_FUNCTION_KEY
npm run dev
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
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment name |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Chat model deployment name |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob storage URL for EAC data |

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