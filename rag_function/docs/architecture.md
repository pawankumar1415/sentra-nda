# Architecture — Custom Approach

---

## System Overview

```mermaid
graph TD
    FE["React Frontend\n(Azure Static Web App — nda-custom-frontend-static)"]
    FE -->|"Authorization: Bearer token"| FA["RAG Function App\n(nda-python-backend)"]

    FA --> PG[("PostgreSQL Flexible Server\npgvector extension\nnda_projects · nda_eac_variance\nchat_sessions · chat_messages")]
    FA --> BLOB[("Azure Blob Storage\nnda-data — EAC Excel files\nguidance — Good Practice DOCX")]
    FA --> AOAI["Azure AI Services\n(under Azure AI Foundry Hub)\ngpt-5.1-chat · text-embedding-3-large"]
```

---

## Request Flow — Validation

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant FE as React Frontend
    participant FA as Function App
    participant EMB as embedder.py
    participant PG as PostgreSQL
    participant GPT as Azure AI Services

    U->>FE: Submit narrative for validation
    FE->>FA: POST /api/validate + Bearer token
    FA->>FA: auth.py — validate JWT
    FA->>EMB: embed(narrative_text)
    EMB->>GPT: text-embedding-3-large
    GPT-->>EMB: 3072-dim vector
    EMB-->>FA: embedding
    FA->>PG: cosine similarity search nda_projects (top-k)
    PG-->>FA: matching project chunks
    FA->>PG: fetch EAC data for project (nda_eac_variance)
    PG-->>FA: EAC variance rows
    FA->>FA: guidance_loader.py — load Good Practice text from Blob
    FA->>GPT: gpt-5.1-chat with prompt (narrative + context + EAC + guidance)
    GPT-->>FA: structured JSON response
    FA-->>FE: { layer1, layer2, rewritten_narrative, overall_verdict }
    FE-->>U: Display results
```

---

## Request Flow — Chat

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React Frontend
    participant FA as Function App
    participant CONV as conversation.py
    participant PG as PostgreSQL
    participant GPT as Azure AI Services

    U->>FE: Send message (first turn)
    FE->>FA: POST /api/chat { question, session_id: null }
    FA->>FA: Validate JWT
    FA->>CONV: create_session() → new UUID
    CONV->>PG: INSERT chat_sessions
    FA->>PG: embed question → cosine search nda_projects
    FA->>CONV: get_history(session_id) — returns [] on first turn
    FA->>GPT: gpt-5.1-chat with question + context + empty history
    GPT-->>FA: answer
    FA->>CONV: save_message(session_id, user, question)
    FA->>CONV: save_message(session_id, assistant, answer)
    CONV->>PG: INSERT chat_messages × 2
    FA-->>FE: { answer, session_id }
    FE->>FE: Store session_id in state

    U->>FE: Send follow-up message
    FE->>FA: POST /api/chat { question, session_id: existing-uuid }
    FA->>CONV: get_history() — returns last 20 messages
    FA->>GPT: gpt-5.1-chat with question + context + history
    GPT-->>FA: answer
    FA-->>FE: { answer, session_id }
```

---

## Database Schema

```mermaid
erDiagram
    users {
        uuid id PK
        text username
        text password_hash
        bool is_admin
        bool is_active
        timestamptz created_at
    }
    nda_projects {
        text project_id PK
        uuid user_id FK
        text project_name
        text period_short_name
        text rag_status
        float eac_variance
        text narrative_text
        vector_3072 embedding
    }
    nda_eac_variance {
        text project_name PK
        uuid user_id PK
        float eac_variance
        text flag
    }
    chat_sessions {
        uuid session_id PK
        uuid user_id FK
        timestamptz created_at
    }
    chat_messages {
        bigint id PK
        uuid session_id FK
        text role
        text content
        timestamptz created_at
    }

    users ||--o{ nda_projects : "uploads"
    users ||--o{ nda_eac_variance : "owns"
    users ||--o{ chat_sessions : "has"
    chat_sessions ||--o{ chat_messages : "contains"
```

**Note:** `nda_projects.project_id` = `"{period}|{project_name}"` — this is a shared table. All users read from the same pool. `user_id` is audit metadata only. `nda_eac_variance` is the only table with true per-user isolation.

---

## Component Responsibilities

| Component | Responsibility |
|---|---|
| `function_app.py` | Route definitions, request parsing, error handling |
| `auth.py` | JWT sign/verify, user registration/login, admin operations |
| `db.py` | Connection pool, schema bootstrap, pgvector search |
| `embedder.py` | Single and batch embedding via Azure OpenAI |
| `ingest.py` | MPPR Excel parsing, project extraction, pgvector upsert |
| `ingest_eac.py` | EAC Excel parsing, threshold calculation, PostgreSQL upsert |
| `validate.py` | Full validation pipeline: embed → retrieve → EAC check → GPT |
| `batch_validate.py` | Loops `validate.py` for every project in an Excel |
| `chat.py` | Conversational RAG: history + retrieval + GPT |
| `conversation.py` | chat_sessions/chat_messages CRUD |
| `sharepoint_client.py` | Lists and downloads files from SharePoint |
| `guidance_loader.py` | Fetches Good Practice DOCX from Azure Blob |