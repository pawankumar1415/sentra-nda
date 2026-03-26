# NDA Narrative Validation System

An AI-powered tool that validates NDA project reporting narratives against writing guidelines and live data movements.

---

## The Two Approaches

| | Custom Approach (`rag_function/`) | Foundry Approach (`agent/`) |
|---|---|---|
| **Backend** | Azure Functions + PostgreSQL + pgvector + OpenAI SDK | Azure Functions + Azure AI Foundry Agents SDK |
| **Search** | pgvector cosine similarity | Azure AI Search (semantic) |
| **Auth** | JWT (login/register, per-user data isolation) | Azure Function key only |
| **UI** | React frontend (`frontend/`) | No UI |
| **Memory** | PostgreSQL chat sessions | Azure AI Foundry thread memory |
| **Status** | Primary — fully featured | Secondary — experimental |

---

## Overall Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       CUSTOM APPROACH                                    │
│                                                                          │
│  React Frontend (frontend/)                                              │
│    /login ──► POST /auth/register|login ──► JWT token                   │
│    /chat   ──► POST /chat  ──► PostgreSQL sessions + GPT                │
│    /validate ─► POST /validate ─► pgvector search + GPT                │
│    /ingest ──► POST /ingest ──────────────────────────┐                 │
│    /admin  ──► GET|POST /admin/users ─────┐           │                 │
│                                           │           │                 │
│  rag_function/ (Azure Function App)       │           │                 │
│  ┌──────────────────────────────────────┐ │           │                 │
│  │  auth.py  — JWT + user management   │ │           │                 │
│  │  ingest.py — Excel → pgvector       │◄┘           │                 │
│  │  validate.py — RAG + GPT            │             │                 │
│  │  chat.py — conversational RAG       │             │                 │
│  │  db.py — PostgreSQL pool            │◄────────────┘                 │
│  └──────────────────────────────────────┘                               │
│              │                       │                                  │
│              ▼                       ▼                                  │
│    Azure PostgreSQL           Azure OpenAI                              │
│    + pgvector                 GPT + Embeddings                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                       FOUNDRY APPROACH                                   │
│                                                                          │
│  agent/ (Azure Function App)                                             │
│    POST /validate ──► Azure AI Foundry Agent ──► Reason + Tools         │
│    POST /chat     ──► Azure AI Foundry Agent ──► Thread memory          │
│    GET /search-projects ──► Azure AI Search                             │
│                                                                          │
│  Resources:                                                              │
│    Azure AI Foundry project: nda-narrative                              │
│    CognitiveServices: movar-secure-azure-resource                       │
│    AI Search: movar-nda-aisearch / index: nda-mppr-projects             │
│    Knowledge base: knowledgebase114 (semantic config on index)          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

```
Custom Solution/
│
├── Implementation Plan Documents/   ← This README + architecture docs
├── NDA Data/                        ← Sample data (gitignored)
│
├── rag_function/                    ← CUSTOM APPROACH backend
│   ├── function_app.py              HTTP routes (auth/admin/data)
│   ├── auth.py                      JWT authentication + user management
│   ├── db.py                        PostgreSQL connection pool + schema
│   ├── embedder.py                  Azure OpenAI embeddings
│   ├── ingest.py                    MPPR Excel parser + PGVector upsert
│   ├── ingest_eac.py                EAC variance Excel parser + upsert
│   ├── validate.py                  Single narrative validation pipeline
│   ├── batch_validate.py            Batch validation pipeline
│   ├── chat.py                      Conversational RAG pipeline
│   ├── conversation.py              Chat session + history management
│   ├── local.settings.json          Local credentials (gitignored)
│   └── requirements.txt             Python dependencies
│
├── frontend/                        ← CUSTOM APPROACH React UI
│   └── src/
│       ├── views/
│       │   ├── LoginView.tsx        Login + Register page
│       │   ├── AdminView.tsx        Admin user management panel
│       │   ├── ChatView.tsx         Conversational chat
│       │   ├── ValidateView.tsx     Individual narrative validation
│       │   ├── BatchValidateView.tsx Batch validation
│       │   └── IngestView.tsx       Data upload
│       ├── components/
│       │   ├── Sidebar.tsx          Nav + user info + logout
│       │   └── ProtectedRoute.tsx   Auth guard (redirects to /login)
│       ├── context/
│       │   └── AuthContext.tsx      JWT state (token/username/is_admin)
│       └── services/
│           └── api.ts               All API calls with auth headers
│
├── agent/                           ← FOUNDRY APPROACH backend
│   ├── function_app.py
│   ├── agent_runner.py
│   ├── tools.py
│   ├── system_prompt.py
│   └── requirements.txt
│
├── test_remote.py                   Remote test for custom validate endpoint
└── test_deployment.py               Remote test for Foundry validate endpoint
```

---

## Azure Resources (Post-Migration to `sellafield-dpmo-dev`)

| Resource | Name | Purpose |
|---|---|---|
| Resource Group | `sellafield-dpmo-dev` | Container for all resources |
| PostgreSQL Flexible Server | (configured in env vars) | Vector store + auth |
| CognitiveServices | `movar-secure-azure-resource` | Azure OpenAI + AI Services |
| AI Search | `movar-nda-aisearch` | Foundry knowledge base |
| AI Search Index | `nda-mppr-projects` | MPPR project index (semantic config) |
| Foundry Project | `nda-narrative` | Foundry agent + knowledge base |
| Function App (Custom) | `nda-python-backend-...` | Custom RAG API |
| Function App (Foundry) | `nda-foundry-api-...` | Foundry agent API |

> **Note:** `movar-secure-azure-resource` was **recreated** (not moved) during the migration. Always fetch the new API keys from Portal → Keys and Endpoint → OpenAI tab.

---

## Getting Started: Custom Approach

### 1. Configure Credentials

Fill in `rag_function/local.settings.json` — see [rag_function/README.md](../rag_function/README.md#environment-variables).

Must include `JWT_SECRET` — a long random string.

### 2. Install Dependencies

```powershell
cd rag_function
pip install -r requirements.txt
```

```powershell
cd frontend
npm install
```

### 3. Run Locally

Backend:
```powershell
cd rag_function
func start
```

Frontend:
```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173` → you will land on `/login`. Register the first account (auto-admin).

### 4. Test the Backend

See [Testing Guide](#testing-guide) below.

### 5. Deploy

```powershell
cd rag_function
func azure functionapp publish nda-python-backend
```

Add `JWT_SECRET` in Azure Portal → Function App → Environment variables.

---

## Getting Started: Foundry Approach

```powershell
cd agent
pip install -r requirements.txt
func start
```

Requires `AZURE_FOUNDRY_PROJECT_ENDPOINT` pointing to `https://movar-secure-azure-resource.services.ai.azure.com/api/projects/nda-narrative`.

---

## Two-Layer Validation

Both approaches share the same validation logic:

### Layer 1 — Guidance & Structure

- Flowing prose (not bullets)
- All required sentences present: DCA/RAG, EAC, schedule, risk/contingency, capability & capacity
- Acronyms expanded on first use
- Full dates (not abbreviations)
- No building numbers, no document references
- Cost figures match reported data

### Layer 2 — Data-Driven Movement Check

| EAC Movement | Flag | Required Action |
|---|---|---|
| < £50k | `none` | No comment required |
| £50k – £0.1m | `minor` | Optional brief mention |
| £0.1m – £0.5m | `material` | Must be explicitly explained |
| ≥ £0.5m | `major` | Requires detailed rationale |
| Schedule slip (positive days) | — | Must be mentioned and explained |

---

## Testing Guide

### Remote Tests (against deployed Azure Function)

**Custom approach:**
```powershell
python test_remote.py --key <HOST_KEY>
```

**Foundry approach:**
```powershell
python test_deployment.py --key <HOST_KEY>
```

### Auth Flow Test Sequence

```powershell
BASE="https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api"
KEY="<your-host-key>"

# 1. Register first user (becomes admin)
curl -X POST "$BASE/auth/register?code=$KEY" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password123"}'

# 2. Login
curl -X POST "$BASE/auth/login?code=$KEY" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password123"}'
# → Copy the "token" value

TOKEN="<paste token here>"

# 3. List users (admin only)
curl "$BASE/admin/users?code=$KEY" -H "Authorization: Bearer $TOKEN"

# 4. Ingest data
curl -X POST "$BASE/ingest?code=$KEY&filename=P07.xlsx" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @P07.xlsx

# 5. Validate a narrative
curl -X POST "$BASE/validate?code=$KEY" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"narrative": "The DCA remains Amber...", "project_name": "Sellafield", "period": "P07"}'

# 6. Chat
curl -X POST "$BASE/chat?code=$KEY" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Which projects are Red RAG?", "session_id": null}'
```