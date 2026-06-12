# Architecture — Foundry Approach

---

## System Overview

```mermaid
graph TD
    CA["Canvas App\n(Power Apps)"]
    PA["Power Automate\n(12 flows)"]
    CA --> PA
    PA -->|"Function host key"| FA["Agent Function App\n(nda-foundry-api)"]

    FA --> PG[("PostgreSQL + pgvector\nnda_projects table")]
    FA --> BLOB[("Azure Blob Storage\nnda-data/conversations/\nnda-data/eac-files/\nguidance/")]

    subgraph aif["Azure AI Foundry Hub"]
        AGENT["Foundry Agent\nnda-narrative-validator-v3"]
        AOAI["Azure AI Services\ngpt-5.1-chat · text-embedding-3-large"]
        AGENT --> AOAI
    end

    FA --> AGENT
    FA --> AOAI
```

---

## Request Flow — Narrative Validation

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant PG as PostgreSQL
    participant AOAI as Azure AI Services
    participant AF as Azure AI Foundry Agent
    participant BLOB as Azure Blob

    CA->>PA: User triggers ValidateNarrativeFlow
    PA->>FA: POST /api/pgvector/validate + host key
    FA->>AOAI: embed(narrative_text) — text-embedding-3-large
    AOAI-->>FA: 3072-dim vector
    FA->>PG: cosine similarity search nda_projects top-k
    PG-->>FA: matching project chunks
    FA->>BLOB: guidance_loader.py — load Good Practice DOCX (cached)
    BLOB-->>FA: guidance text
    FA->>BLOB: Load EAC data from blob (if present)
    BLOB-->>FA: EAC file bytes
    FA->>AF: pgvector_backend.validate_narrative_pgvector(narrative, context, eac, guidance)
    AF->>AOAI: gpt-5.1-chat with full prompt
    AOAI-->>AF: validation response
    AF-->>FA: agent response text
    FA->>BLOB: Save conversation blob nda-data/conversations/<uuid>.json
    FA-->>PA: { response, conversation_id }
    PA-->>CA: Display result
```

---

## Request Flow — MPPR Ingest

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant AOAI as Azure AI Services
    participant PG as PostgreSQL

    CA->>PA: User uploads MPPR file, triggers IngestMPPRFlow
    PA->>FA: POST /api/pgvector/ingest-mppr (Excel bytes) + host key
    FA->>FA: Parse 5a)NDA MPPR sheet
    FA->>FA: Extract project rows (data + narrative)
    FA->>AOAI: embed_batch(raw_content list) — text-embedding-3-large
    AOAI-->>FA: list of 3072-dim vectors
    FA->>PG: UPSERT nda_projects ON CONFLICT (project_id) DO UPDATE
    PG-->>FA: rows affected
    FA-->>PA: { status: ok, indexed: N }
    PA-->>CA: Confirmation message
```

---

## Conversation Memory

Short-term memory is stored as JSON blobs in Azure Blob Storage.

```mermaid
graph LR
    CALL1["First validate/chat call\nno conversation_id"] --> CREATE["Generate UUID\nCreate new conversation blob"]
    CREATE --> BLOB[("nda-data/conversations/uuid.json\n{ messages: [] }")]
    BLOB --> RETURN["Return conversation_id to caller"]

    CALL2["Follow-up call\nwith conversation_id"] --> LOAD["Load blob for UUID"]
    LOAD --> INJECT["Inject message history\ninto agent prompt"]
    INJECT --> AGENT["Foundry Agent"]
    AGENT --> SAVE["Append new messages\nSave blob"]
    SAVE --> BLOB
```

Non-fatal: if the blob write fails, validation still completes. History is lost for that session but no error is returned to the caller.

---

## pgvector vs Legacy Routes

The function app contains two sets of routes:

```mermaid
graph LR
    REQ["Incoming request"] --> CHECK{Route prefix}
    CHECK -->|"/api/pgvector/*"| PGV["pgvector_backend.py\nActive — used by all 12 flows"]
    CHECK -->|"/api/pa-batch-validate"| PA["pa_batch_validate_pgvector\nActive — Power Automate batch"]
    CHECK -->|"/api/validate /api/chat etc"| LEGACY["Legacy routes\nAI Search backed\nNot used by current flows"]
```

All 12 Power Automate flows call either `/api/pgvector/*` routes or `/api/pa-batch-validate`. Legacy routes remain in the code for reference but are not called.

---

## Component Responsibilities

| Component | Responsibility |
|---|---|
| `function_app.py` | Route definitions, request parsing, error handling |
| `pgvector_backend.py` | pgvector-backed validation, chat, ingest — what flows actually call |
| `agent_runner.py` | Foundry agent execution, blob conversation memory management |
| `batch_validate.py` | Loops validation per project for batch calls |
| `chat.py` | Chat wrapper — manages conversation blob and calls the agent |
| `tools.py` | Tool functions the Foundry agent calls (EAC lookup, schedule check) |
| `system_prompt.py` | Agent system prompt text |
| `ingest_helper.py` | AI Search indexing and search (legacy, not used by current flows) |
| `guidance_loader.py` | Fetches Good Practice DOCX from Blob (cached per cold start) |
| `config.py` | Centralised env var reading — all other modules import from here |
| `create_agent.py` | One-time script to create the Foundry agent (not part of runtime) |
| `setup_memory.py` | CLI for Foundry Memory Store setup (not used in production) |