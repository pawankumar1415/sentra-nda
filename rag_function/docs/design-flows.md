# Design Flows — Custom Approach

Step-by-step flows from user action to response for every key operation.

---

## 1. User Registration

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /login
    participant FA as Function App
    participant DB as PostgreSQL

    U->>FE: Fill in username + password, click Register
    FE->>FA: POST /api/auth/register { username, password }
    FA->>FA: Validate: username ≥ 3 chars, password ≥ 8 chars
    FA->>DB: SELECT COUNT(*) FROM users WHERE is_active = TRUE
    DB-->>FA: count (0 = first user)
    FA->>FA: Hash password (PBKDF2-HMAC-SHA256, 310k iterations)
    FA->>DB: INSERT INTO users (username, hash, is_admin)
    DB-->>FA: new user_id
    FA->>FA: Sign JWT { user_id, username, is_admin, exp: +8h }
    FA-->>FE: 201 { token, username, is_admin }
    FE->>FE: Store token + username + is_admin in localStorage
    FE->>FE: Redirect to /validate
```

---

## 2. User Login

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /login
    participant FA as Function App
    participant DB as PostgreSQL

    U->>FE: Enter username + password, click Sign In
    FE->>FA: POST /api/auth/login { username, password }
    FA->>DB: SELECT id, password_hash, is_admin, is_active WHERE username = ?
    DB-->>FA: user row
    FA->>FA: Check is_active — if false, return 403
    FA->>FA: Verify PBKDF2 hash
    FA->>FA: Sign JWT
    FA-->>FE: 200 { token, username, is_admin }
    FE->>FE: Store in localStorage, redirect to /validate
```

---

## 3. MPPR Data Ingest

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /ingest
    participant FA as Function App
    participant EMB as embedder.py
    participant AOAI as Azure AI Services
    participant DB as PostgreSQL

    U->>FE: Select MPPR Excel file, click Upload
    FE->>FA: POST /api/ingest (multipart file) + Bearer token
    FA->>FA: require_auth() — verify JWT, extract user_id
    FA->>FA: ingest.py — parse 5a)NDA MPPR sheet
    FA->>FA: Extract project rows (data row + narrative row)
    FA->>EMB: embed_batch(raw_content list)
    EMB->>AOAI: text-embedding-3-large (all projects in one call)
    AOAI-->>EMB: list of 3072-dim vectors
    EMB-->>FA: embeddings
    FA->>DB: UPSERT nda_projects ON CONFLICT (project_id) DO UPDATE
    DB-->>FA: rows affected
    FA-->>FE: 200 { status: ok, period, indexed: N }
    FE-->>U: "Indexed N projects for period P07"
```

---

## 4. Single Narrative Validation

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /validate
    participant FA as Function App
    participant EMB as embedder.py
    participant AOAI as Azure AI Services
    participant DB as PostgreSQL
    participant BLOB as Azure Blob

    U->>FE: Select project, paste/edit narrative, click Validate
    FE->>FA: POST /api/validate { narrative, project_name, period } + Bearer token
    FA->>FA: require_auth() — verify JWT
    FA->>EMB: embed(narrative)
    EMB->>AOAI: text-embedding-3-large
    AOAI-->>EMB: 3072-dim vector
    FA->>DB: Cosine similarity search nda_projects (top-k=5)
    DB-->>FA: Matching project chunks
    FA->>DB: SELECT * FROM nda_eac_variance WHERE project_name = ? AND user_id = ?
    DB-->>FA: EAC variance row + flag
    FA->>BLOB: guidance_loader.py — fetch Good Practice DOCX (cached after first call)
    BLOB-->>FA: guidance text
    FA->>AOAI: gpt-5.1-chat (narrative + context + EAC + guidance rules)
    AOAI-->>FA: Structured JSON validation result
    FA->>FA: Parse Layer 1 (format) + Layer 2 (data) results
    FA-->>FE: { layer1, layer2, rewritten_narrative, overall_verdict }
    FE-->>U: Display compliance score, issues, rewritten narrative
```

---

## 5. Conversational Chat — First Message

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /chat
    participant FA as Function App
    participant CONV as conversation.py
    participant DB as PostgreSQL
    participant AOAI as Azure AI Services

    U->>FE: Type question, press Enter (no existing session)
    FE->>FA: POST /api/chat { question, session_id: null } + Bearer token
    FA->>FA: require_auth() — verify JWT
    FA->>CONV: create_session(user_id) → new UUID
    CONV->>DB: INSERT INTO chat_sessions
    FA->>DB: embed question → cosine search nda_projects top-5
    DB-->>FA: relevant project chunks
    FA->>CONV: get_history(session_id) → [] (new session)
    FA->>AOAI: gpt-5.1-chat (question + context + empty history)
    AOAI-->>FA: answer
    FA->>CONV: save_message(session_id, "user", question)
    FA->>CONV: save_message(session_id, "assistant", answer)
    CONV->>DB: INSERT INTO chat_messages × 2
    FA-->>FE: { answer, session_id }
    FE->>FE: Store session_id in component state
    FE-->>U: Display answer
```

---

## 6. Conversational Chat — Follow-up Message

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /chat
    participant FA as Function App
    participant CONV as conversation.py
    participant DB as PostgreSQL
    participant AOAI as Azure AI Services

    U->>FE: Type follow-up question (session already exists)
    FE->>FA: POST /api/chat { question, session_id: existing-uuid } + Bearer token
    FA->>FA: require_auth()
    FA->>DB: embed question → cosine search
    FA->>CONV: get_history(session_id, limit=20) → last 20 messages
    CONV->>DB: SELECT FROM chat_messages ORDER BY created_at DESC LIMIT 20
    DB-->>CONV: message rows
    FA->>AOAI: gpt-5.1-chat (question + context + last 20 messages)
    AOAI-->>FA: answer (with awareness of prior conversation)
    FA->>CONV: save_message × 2
    FA-->>FE: { answer, session_id }
    FE-->>U: Display answer
```

---

## 7. Batch Validation

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /batch-validate
    participant FA as Function App
    participant VAL as validate.py
    participant DB as PostgreSQL
    participant AOAI as Azure AI Services

    U->>FE: Upload MPPR Excel, click Run Batch
    FE->>FA: POST /api/batch-validate (Excel file) + Bearer token
    FA->>FA: require_auth()
    FA->>FA: Parse Excel → extract project list
    loop For each project
        FA->>VAL: run_validate(narrative, project_name, period, user_id)
        VAL->>DB: embed → search → EAC fetch
        VAL->>AOAI: gpt-5.1-chat
        AOAI-->>VAL: result
        VAL-->>FA: { layer1, layer2, verdict }
    end
    FA-->>FE: [ result per project ]
    FE-->>U: Progress bar completes, results table shown
```

---

## 8. SharePoint File Browse

```mermaid
sequenceDiagram
    participant U as User
    participant FE as React /ingest
    participant FA as Function App
    participant SP as SharePoint

    U->>FE: Click "Browse SharePoint"
    FE->>FA: GET /api/sharepoint/files + Bearer token
    FA->>FA: require_auth()
    FA->>SP: sharepoint_client.list_files() using Entra ID app credentials
    SP-->>FA: List of Excel files in SentraFileStaging library
    FA-->>FE: [ { name, url, modified } ]
    FE-->>U: File picker populated with SharePoint files

    U->>FE: Select file, click Extract Projects
    FE->>FA: POST /api/sharepoint/list-projects { file_url } + Bearer token
    FA->>SP: sharepoint_client.download_file(url)
    SP-->>FA: Excel file bytes
    FA->>FA: ingest.py.list_projects_from_bytes() — parse without DB write
    FA-->>FE: [ project names ]
    FE-->>U: Project list displayed
```