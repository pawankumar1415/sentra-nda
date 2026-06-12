# API Endpoints — Custom Approach

Base URL: `https://nda-python-backend.azurewebsites.net`

All requests must include the Azure Function host key as `?code=<key>` or `x-functions-key` header. Data routes additionally require `Authorization: Bearer <jwt-token>`.

---

## Auth — public

### POST `/api/auth/register`
Create a new user account.

**Request:**
```json
{ "username": "alice", "password": "password123" }
```

**Response 201:**
```json
{ "token": "eyJ...", "username": "alice", "is_admin": true }
```

First registered user is automatically admin. All subsequent users are standard users.

---

### POST `/api/auth/login`
Authenticate an existing user.

**Request:**
```json
{ "username": "alice", "password": "password123" }
```

**Response 200:**
```json
{ "token": "eyJ...", "username": "alice", "is_admin": false }
```

Token expires after 8 hours. Returns 403 if the account is deactivated.

---

## Admin — admin JWT required

### GET `/api/mgmt/users`
List all users with project and session counts.

**Response 200:**
```json
{
  "users": [
    {
      "user_id": "uuid",
      "username": "alice",
      "is_admin": true,
      "is_active": true,
      "created_at": "2026-01-15T09:00:00Z",
      "projects_count": 42,
      "sessions_count": 7
    }
  ]
}
```

---

### POST `/api/mgmt/users/update`
Toggle `is_active` or `is_admin` for a user. Admin cannot modify their own account.

**Request:**
```json
{ "user_id": "uuid", "is_active": false }
```
or
```json
{ "user_id": "uuid", "is_admin": true }
```

**Response 200:**
```json
{ "updated": true, "user_id": "uuid", "is_active": false }
```

---

### POST `/api/mgmt/users/delete`
Delete a user and all their data (projects, EAC, chat sessions, messages).

**Request:**
```json
{ "user_id": "uuid" }
```

**Response 200:**
```json
{ "deleted": true, "user_id": "uuid", "username": "alice" }
```

---

## Data — auth JWT required

### POST `/api/ingest`
Upload MPPR Excel → parse → embed → upsert into pgvector.

Send as `multipart/form-data` with field `file`, or as `application/octet-stream` body with `?filename=` query param.

**Response 200:**
```json
{ "status": "ok", "period": "P07", "indexed": 42 }
```

---

### POST `/api/ingest-eac`
Upload EAC variance Excel → parse thresholds → upsert into `nda_eac_variance` (per user).

Send same as `/api/ingest`.

**Response 200:**
```json
{ "status": "ok", "rows_upserted": 38 }
```

---

### POST `/api/validate`
Validate a single narrative. Returns structured two-layer result.

**Request:**
```json
{
  "narrative": "The SRO DCA remains Amber because...",
  "project_name": "Security Systems Architecture",
  "period": "P07",
  "top_k": 5
}
```

**Response 200:**
```json
{
  "layer1": {
    "compliance_score": 8,
    "issues": ["Missing acronym expansion for SRO"],
    "passed": ["Correct date format", "Building number present"]
  },
  "layer2": {
    "eac_explained": true,
    "schedule_explained": false,
    "data_flag": "material",
    "issues": ["Schedule slippage of 14 days not mentioned in narrative"]
  },
  "rewritten_narrative": "The SRO (Senior Responsible Owner)...",
  "overall_verdict": "PASS_WITH_WARNINGS",
  "_meta": {
    "project_name": "Security Systems Architecture",
    "chunks_used": 5,
    "eac_flag": "material",
    "eac_variance_m": 0.3
  }
}
```

---

### POST `/api/chat`
Conversational RAG. Pass `session_id: null` on first message; server creates a session and returns the UUID. Send the UUID on every subsequent message to maintain context.

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
  "answer": "## Red RAG Projects\n\n- **Alpha Programme** — EAC variance £1.2m...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### POST `/api/list-projects`
Parse project names from an uploaded Excel. No database write.

**Response 200:**
```json
{ "projects": ["Project Alpha", "Project Beta", "..."] }
```

---

### POST `/api/batch-validate`
Validate every narrative in an uploaded Excel. Returns one result per project.

**Response 200:**
```json
{
  "results": [
    {
      "project_name": "Alpha",
      "overall_verdict": "PASS",
      "layer1": { "compliance_score": 9 },
      "layer2": { "eac_explained": true }
    }
  ]
}
```

---

### GET `/api/search-projects?q=<term>&limit=20`
Fuzzy search against indexed project names in pgvector.

**Response 200:**
```json
{ "projects": ["Security Systems Architecture", "Sellafield Ltd"] }
```

---

### GET `/api/sharepoint/files`
List Excel files from the SentraFileStaging SharePoint library.

**Response 200:**
```json
{
  "files": [
    { "name": "NDA_MPPR_P07.xlsx", "url": "https://...", "modified": "2026-06-01T10:00:00Z" }
  ]
}
```

---

### POST `/api/sharepoint/list-projects`
Download a specific SharePoint file and extract project names without writing to the database.

**Request:**
```json
{ "file_url": "https://sharepoint.com/..." }
```

**Response 200:**
```json
{ "projects": ["Project Alpha", "Project Beta"] }
```