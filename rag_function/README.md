# rag_function — NDA Narrative Validation (Custom Approach)

A self-contained RAG pipeline for validating NDA project narrative text, built on **Azure Functions v2 + PostgreSQL pgvector + Azure OpenAI**. Includes JWT-based user authentication, per-user data isolation, conversational memory, and an admin panel.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Folder Structure](#folder-structure)
4. [Environment Variables](#environment-variables)
5. [Database Schema](#database-schema)
6. [API Reference](#api-reference)
7. [Authentication](#authentication)
8. [Local Testing](#local-testing)
9. [Deployment to Azure](#deployment-to-azure)
10. [Troubleshooting](#troubleshooting)

---

## Overview

The pipeline performs **two-layer validation** of NDA project narratives:

- **Layer 1 — Guidance & Structure:** Checks prose flow, sentence templates, acronym expansion, date formats, building number policy.
- **Layer 2 — Data-Driven:** Checks whether material EAC movements (≥ £0.1m) and schedule slippages are explained in the narrative.

Data is retrieved from a **PostgreSQL vector store** populated by ingesting NDA MPPR Excel files. All data is **scoped per authenticated user** so each user only sees and searches their own uploaded data.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Azure Function App (Custom Approach)              │
│                                                                     │
│  Auth Routes        Admin Routes          Data Routes               │
│  POST /auth/register  GET /admin/users     POST /ingest             │
│  POST /auth/login     POST /admin/users/   POST /ingest-eac         │
│                         update             POST /validate           │
│                       POST /admin/users/   POST /chat               │
│                         delete             POST /list-projects      │
│                                            GET  /search-projects    │
│                                            POST /batch-validate     │
│                                                                     │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│   │ auth.py  │  │ingest.py │  │validate  │  │    chat.py       │  │
│   │ JWT/PBKDF│  │Excel→vec │  │  .py     │  │ conversation.py  │  │
│   └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│         │              │             │                │             │
│   ┌─────▼──────────────▼─────────────▼────────────────▼──────────┐ │
│   │                          db.py                                │ │
│   │            PostgreSQL + pgvector connection pool              │ │
│   └───────────────────────────────────────────────────────────────┘ │
│         │                                                           │
│   ┌─────▼──────┐                                                   │
│   │ embedder.py│ Azure OpenAI text-embedding-3-large               │
│   └────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────┘
              │                           │
              ▼                           ▼
    Azure PostgreSQL               Azure OpenAI
    + pgvector                     GPT + Embeddings
```

---

## Folder Structure

```
rag_function/
│
├── function_app.py       Azure Functions v2 entry point — all HTTP routes
├── auth.py               JWT authentication + user management
├── db.py                 PostgreSQL connection pool + schema bootstrap
├── embedder.py           Azure OpenAI embedding wrapper (single + batch)
├── ingest.py             MPPR Excel parser + PGVector upsert
├── ingest_eac.py         EAC variance Excel parser + upsert
├── validate.py           Single narrative validation pipeline
├── batch_validate.py     Batch validation pipeline
├── chat.py               Conversational RAG pipeline
├── conversation.py       PostgreSQL-backed session/history management
│
├── requirements.txt      Python dependencies
├── host.json             Azure Functions host configuration
└── local.settings.json   Local environment variables (gitignored — never commit)
```

---

## Environment Variables

All variables live in `rag_function/local.settings.json` locally, and in **Azure Function App → Settings → Environment variables** in production.

### PostgreSQL

| Variable | Required | Example |
|---|---|---|
| `POSTGRES_HOST` | ✅ | `myserver.postgres.database.azure.com` |
| `POSTGRES_DB` | ✅ | `nda_agent` |
| `POSTGRES_USER` | ✅ | `nda_admin` |
| `POSTGRES_PASSWORD` | ✅ | (leave empty to use Managed Identity token) |
| `POSTGRES_PORT` | ❌ | `5432` (default) |
| `POSTGRES_SSL` | ❌ | `require` (default) |

### Azure OpenAI

| Variable | Required | Example |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | ✅ | `https://myresource.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | ✅ | `••••••` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | ❌ | `text-embedding-3-large` |
| `AZURE_OPENAI_EMBEDDING_DIMS` | ❌ | `3072` (default, must match deployment) |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | ❌ | `gpt-5.1-chat` |
| `AZURE_OPENAI_API_VERSION` | ❌ | `2024-02-01` |

### Authentication

| Variable | Required | Notes |
|---|---|---|
| `JWT_SECRET` | ✅ | Long random string — sign all JWT tokens. **Must be set before first use.** Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |

---

## Database Schema

All tables are created automatically on cold-start by `ensure_schema()` in `db.py`. Migrations run alongside creation — the system is fully idempotent.

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Auto-generated |
| `username` | TEXT UNIQUE | Lowercase, min 3 chars |
| `password_hash` | TEXT | PBKDF2-HMAC-SHA256, 310k iterations |
| `is_admin` | BOOLEAN | First registered user is auto-admin |
| `is_active` | BOOLEAN | Inactive users cannot log in |
| `created_at` | TIMESTAMPTZ | |

### `nda_projects`

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT PK | `"{period}\|{project_name}"` — shared across all users |
| `user_id` | UUID FK → users | Stored as audit metadata only — not used for query scoping |
| `project_name` | TEXT | |
| `period_short_name` | TEXT | e.g. `P07` |
| `rag_status` | TEXT | R/A/G |
| `dca_rag_status` | TEXT | |
| `eac_total` | FLOAT | £m |
| `eac_variance` | FLOAT | £m vs previous period |
| `schedule_variance_days` | INT | |
| `narrative_text` | TEXT | |
| `raw_content` | TEXT | Concatenated text chunk used for embedding |
| `embedding` | vector(3072) | text-embedding-3-large output |
| `indexed_at` | TIMESTAMPTZ | |

### `nda_eac_variance`

| Column | Type | Notes |
|---|---|---|
| `project_name` | TEXT | Composite PK with user_id |
| `user_id` | UUID FK → users | Composite PK — data isolated per user |
| `period_short_name` | TEXT | |
| `eac_variance` | FLOAT | £ (stored in full pounds, not £m) |
| `schedule_variance_days` | INT | |
| `flag` | TEXT | `none` / `minor` / `material` / `major` |
| `summary_text` | TEXT | |
| `updated_at` | TIMESTAMPTZ | |

### `chat_sessions` / `chat_messages`

| Table | Key columns | Notes |
|---|---|---|
| `chat_sessions` | `session_id` UUID PK, `user_id` FK | One session per conversation |
| `chat_messages` | `id` BIGSERIAL PK, `session_id` FK, `role`, `content` | Full history retained; last 20 loaded per LLM call |

---

## API Reference

All data routes require a valid JWT in the `Authorization: Bearer <token>` header.

### Auth Routes (public)

#### POST /api/auth/register

```json
// Request
{ "username": "alice", "password": "supersecret123" }

// Response 201
{ "token": "eyJ...", "username": "alice", "is_admin": true }
```

> The **first registered user** is automatically granted admin. Subsequent users are normal users.

#### POST /api/auth/login

```json
// Request
{ "username": "alice", "password": "supersecret123" }

// Response 200
{ "token": "eyJ...", "username": "alice", "is_admin": true }
```

Tokens expire after **8 hours**. The frontend stores the token in `localStorage` and refreshes by calling `/login` again.

---

### Admin Routes (admin JWT required)

#### GET /api/mgmt/users

Returns all users with stats.

```json
{
  "users": [
    {
      "user_id": "uuid",
      "username": "alice",
      "is_admin": true,
      "is_active": true,
      "created_at": "2025-01-15T09:00:00Z",
      "projects_count": 42,
      "sessions_count": 7
    }
  ]
}
```

#### POST /api/mgmt/users/update

```json
{ "user_id": "uuid", "is_active": false }
// or
{ "user_id": "uuid", "is_admin": true }
```

#### POST /api/mgmt/users/delete

```json
{ "user_id": "uuid" }
```

Deletes the user and **all their data** (projects, EAC, chat sessions/messages) in dependency order.

---

### Data Routes (auth JWT required)

#### POST /api/ingest

Upload MPPR Excel → embed → upsert to pgvector (scoped to authenticated user).

Send as `multipart/form-data` field `file`, or raw `application/octet-stream` body with `?filename=` param.

```json
// Response 200
{ "status": "ok", "period": "P07", "indexed": 42 }
```

#### POST /api/ingest-eac

Upload EAC variance Excel → upsert to `nda_eac_variance` (scoped to user).

#### POST /api/validate

```json
// Request
{
  "narrative": "The SRO DCA remains Amber because...",
  "project_name": "Security Systems Architecture",
  "period": "P07",
  "top_k": 5
}

// Response 200
{
  "layer1": { "compliance_score": 8, "issues": [...], "passed": [...] },
  "layer2": { "eac_explained": true, "schedule_explained": true, "data_flag": "material", "issues": [] },
  "rewritten_narrative": "...",
  "overall_verdict": "PASS_WITH_WARNINGS",
  "_meta": { "project_name": "...", "chunks_used": 5, "eac_flag": "material", "eac_variance_m": 0.3 }
}
```

#### POST /api/chat

```json
// Request
{
  "question": "Which projects are Red RAG this period?",
  "session_id": "uuid-or-null"
}

// Response 200
{
  "answer": "## Red RAG Projects\n...",
  "session_id": "new-or-existing-uuid",
  "meta": { "intent": "portfolio_summary", "projects_detected": [], "is_new_session": false }
}
```

Send `session_id: null` on first message; the server creates a session and returns the UUID. Store it in `localStorage` and pass it on every subsequent message.

#### GET /api/search-projects?q=<term>&limit=20

Returns user's indexed projects matching the query (fuzzy name search).

#### POST /api/list-projects

Parses an Excel file and returns the project list without writing to the database.

#### POST /api/batch-validate

Validates every narrative in an uploaded MPPR Excel file. Returns an array of validation results, one per project.

---

## Authentication

### How It Works

1. **Register** once via `POST /api/auth/register`. The first account created is automatically admin.
2. **Login** via `POST /api/auth/login` to get a JWT token (valid 8 hours).
3. **Every subsequent request** must include `Authorization: Bearer <token>` header.
4. **Tokens expire** — the frontend redirects to `/login` on a 401 response.

### Password Security

Passwords are hashed with **PBKDF2-HMAC-SHA256** at 310,000 iterations (OWASP 2024 recommendation). No external bcrypt dependency needed — uses Python stdlib `hashlib`.

### Data Isolation

- `nda_projects` is a **shared table** — all users read from and write to the same pool. `user_id` is stored as audit metadata only. Two users uploading the same period will overwrite the same rows.
- `nda_eac_variance` has a **composite PK** `(project_name, user_id)` — EAC data is per user.
- `chat_sessions` stores `user_id` — conversation history is private per user.
- `users` table is fully isolated — admin operations are scoped to specific user IDs.

---

## Local Testing

### Prerequisites

Fill in `rag_function/local.settings.json`:

```json
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "POSTGRES_HOST": "...",
    "POSTGRES_DB": "...",
    "POSTGRES_USER": "...",
    "POSTGRES_PASSWORD": "...",
    "AZURE_OPENAI_ENDPOINT": "...",
    "AZURE_OPENAI_API_KEY": "...",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
    "AZURE_OPENAI_EMBEDDING_DIMS": "3072",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-5.1-chat",
    "JWT_SECRET": "your-random-secret-here"
  }
}
```

### Running the Function Locally

```powershell
cd rag_function
func start
```

The function will be available at `http://localhost:7071/api/`.

### Test Sequence

1. **Register first user** (will become admin):
   ```powershell
   curl -X POST http://localhost:7071/api/auth/register `
     -H "Content-Type: application/json" `
     -d '{"username": "admin", "password": "password123"}'
   ```

2. **Copy the token** from the response.

3. **Test a protected endpoint**:
   ```powershell
   curl http://localhost:7071/api/mgmt/users `
     -H "Authorization: Bearer <token>"
   ```

4. **Upload MPPR data**:
   ```powershell
   curl -X POST "http://localhost:7071/api/ingest?filename=P07.xlsx" `
     -H "Authorization: Bearer <token>" `
     -H "Content-Type: application/octet-stream" `
     --data-binary @"P07.xlsx"
   ```

5. **Validate a narrative**:
   ```powershell
   curl -X POST http://localhost:7071/api/validate `
     -H "Authorization: Bearer <token>" `
     -H "Content-Type: application/json" `
     -d '{"narrative": "The DCA remains Amber...", "project_name": "Sellafield", "period": "P07"}'
   ```

---

## Deployment to Azure

### 1. Add JWT_SECRET to Function App Settings

In Azure Portal → **nda-python-backend → Settings → Environment variables**, add:

| Name | Value |
|---|---|
| `JWT_SECRET` | A long random string (min 32 chars). Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |

### 2. Deploy Code

```powershell
cd rag_function
func azure functionapp publish nda-python-backend
```

Or via VS Code: **Azure extension → Deploy to Function App**.

### 3. Test Auth on the Deployed App

```powershell
# Replace <KEY> with your Azure Function host key

# Register
curl -X POST "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api/auth/register?code=<KEY>" `
  -H "Content-Type: application/json" `
  -d '{"username": "admin", "password": "yourpassword"}'

# Login
curl -X POST "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api/auth/login?code=<KEY>" `
  -H "Content-Type: application/json" `
  -d '{"username": "admin", "password": "yourpassword"}'
```

---

## Troubleshooting

### `401 Missing or invalid Authorization header`
- All data routes now require a `Bearer` token. Call `/auth/login` first and include the token.

### `403 Admin access required`
- The endpoint requires an admin account. Log in with the first registered account (auto-admin), or promote a user via `/admin/users/update`.

### `psycopg2.OperationalError: could not connect to server`
- Check `POSTGRES_HOST`, firewall rules allow Azure Services, and `POSTGRES_SSL=require`.

### `JWT_SECRET` not set — tokens decode with default secret
- The function falls back to `"change-me-in-production"` if `JWT_SECRET` is missing. **Always set a real secret in production.**

### Empty retrieval results after auth migration
- Old data uploaded before auth was added has `user_id = NULL`. Re-upload your MPPR file after logging in to populate user-scoped data.

### `extension "vector" is not available`
- Enable pgvector: Azure Portal → PostgreSQL server → Server Parameters → `azure.extensions` → add `VECTOR`.