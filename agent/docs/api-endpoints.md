# API Endpoints — Foundry Approach

Base URL: `https://nda-foundry-api.azurewebsites.net`

All requests require the Azure Function host key as `?code=<key>` or `x-functions-key` header. No per-user JWT — all callers are Power Automate flows using a shared host key.

---

## Active Routes — pgvector backed

These are the routes called by all current Power Automate flows. All business logic goes through `pgvector_backend.py`.

---

### POST `/api/pgvector/ingest-mppr`

Upload an MPPR Excel file. Parses the `5a)NDA MPPR` sheet, embeds all project narratives using `text-embedding-3-large`, and upserts into `nda_projects` in PostgreSQL.

**Body:** raw binary (octet-stream) or multipart/form-data with `file` field.

**Query params:** `filename` (optional), `reporting_period` (optional — auto-detected from Excel if absent).

**Response 200:**
```json
{ "status": "ok", "reporting_period": "P07", "indexed": 42 }
```

Called by: **IngestMPPRFlow**, **IngestMPPRLocalFlow**

---

### POST `/api/pgvector/ingest-eac`

Upload EAC variance Excel to Azure Blob Storage. The EAC file is stored in `nda-data/eac-files/`. The agent reads it from Blob on each validation call.

**Body:** raw binary or multipart `file` field.

**Response 200:**
```json
{ "status": "ok", "blob_name": "eac-files/lifecycle_eac_variance.xlsx" }
```

Called by: **IngestEACFlow**, **IngestEACLocalFlow**

---

### POST `/api/pgvector/validate`

Validate a single narrative. Embeds the narrative → cosine searches `nda_projects` → loads EAC from Blob → loads Good Practice guidance (cached) → calls the Foundry agent with full context.

**Request:**
```json
{
  "project_name": "Security Systems Architecture",
  "narrative": "The SRO DCA remains Amber because...",
  "period": "P07",
  "conversation_id": null,
  "user_scope": null
}
```

`conversation_id`: pass `null` on first call. Server returns a UUID; pass it back on follow-up calls to continue the conversation.

**Response 200:**
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "is_new_conversation": true,
  "validation_result": "## Layer 1 Compliance\n\n**Score: 8/10**\n\n..."
}
```

Called by: **ValidateNarrativeFlow**, **ValidateNarrativeLocalFlow**

---

### POST `/api/pgvector/batch-validate`

Validate every narrative in an uploaded MPPR Excel. Loops `validate_narrative_pgvector()` per project and returns one result per row.

**Body:** raw binary or multipart `file` field.

**Response 200:**
```json
{
  "status": "ok",
  "period": "P07",
  "total": 42,
  "results": [
    {
      "project_name": "Security Systems Architecture",
      "status": "ok",
      "validation_result": "...",
      "conversation_id": "uuid"
    }
  ]
}
```

---

### POST `/api/pgvector/chat`

Conversational assistant backed by pgvector context and the Foundry agent. Pass `session_id: null` on first message; include the returned UUID on follow-ups to maintain continuity.

Conversation history is stored in `nda-data/conversations/<uuid>.json` in Azure Blob.

**Request:**
```json
{
  "question": "Which projects are Red RAG this period?",
  "session_id": null
}
```

**Response 200:**
```json
{
  "answer": "## Red RAG Projects\n\n- **Alpha Programme** — ...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Called by: **ChatWithNDADataFlow**, **ChatWithNDADataLocalFlow**

---

### POST `/api/pgvector/list-projects`

Parse project names from an uploaded MPPR Excel. No database write — used to populate dropdowns in Canvas App before selecting a project to validate.

**Body:** raw binary Excel.

**Response 200:**
```json
{
  "period": "P07",
  "projects": [
    { "project_name": "Alpha Programme", "narrative_text": "..." }
  ]
}
```

Called by: **ListProjectsFromSPFlow**, **ListProjectsLocalFlow**

---

### GET `/api/pgvector/search-projects?q=<term>&limit=20`

Fuzzy text search against project names indexed in `nda_projects`. Returns up to `limit` matches (max 50).

**Response 200:**
```json
{ "projects": ["Security Systems Architecture", "Sellafield Ltd"] }
```

---

### GET `/api/pgvector/history`

Retrieve validation history from the `validation_history` table.

**Query params:** `project_name` (optional partial match), `limit` (default 50, max 200), `offset` (pagination).

**Response 200:**
```json
{
  "total": 120,
  "items": [
    {
      "id": "uuid",
      "project_name": "Alpha Programme",
      "period": "P07",
      "narrative": "...",
      "rewritten_narrative": "...",
      "compliance_score": 8,
      "overall_verdict": "PASS",
      "issues": ["..."],
      "validated_at": "2026-06-01T10:00:00+00:00"
    }
  ]
}
```

---

### GET `/api/pgvector/migrate`

One-time migration — creates the `validation_history` table if it does not exist. Safe to call multiple times (uses `CREATE TABLE IF NOT EXISTS`). Run this after deploying to a new environment.

**Response 200:**
```json
{ "status": "ok", "message": "validation_history table ensured." }
```

---

## Special Route — Power Automate Batch

### POST `/api/pa-batch-validate`

Power Automate–specific batch endpoint. Returns a flat `csv_rows` array and summary counts instead of the nested `results` object returned by `/api/pgvector/batch-validate`. This flatter format is easier to process inside a Power Automate Apply to Each loop.

**Body:** raw binary or multipart `file` field.

**Response 200:**
```json
{
  "status": "ok",
  "period": "P07",
  "total": 42,
  "passed": 30,
  "warnings": 8,
  "failed": 4,
  "csv_rows": [
    {
      "project_name": "Alpha Programme",
      "overall_verdict": "PASS",
      "compliance_score": 9,
      "validation_result": "..."
    }
  ]
}
```

Called by: **BatchValidateFlow**, **BatchValidateLocalFlow**

---

## Legacy Routes — AI Search backed

These routes exist in the code but are **not called by any current Power Automate flows**. They use Azure AI Search for retrieval and `agent_runner.validate_narrative()` for generation. Retained for reference only.

| Route | Description |
|---|---|
| `POST /api/ingest-mppr` | Upload MPPR → AI Search index |
| `POST /api/ingest-eac` | Upload EAC → Azure Blob |
| `GET /api/search-projects` | Wildcard search in AI Search index |
| `POST /api/list-projects` | Parse project names from Excel (no AI call) |
| `POST /api/validate` | Validate single narrative via AI Search + Foundry agent |
| `POST /api/batch-validate` | Batch validate via AI Search + Foundry agent |
| `POST /api/chat` | Chat via AI Search + Blob session memory |