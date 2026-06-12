# Design Flows — Foundry Approach

Step-by-step sequence diagrams showing how each request travels from the Canvas App through Power Automate to the Function App and back.

---

## 1. MPPR Ingest

User uploads an Excel file in the Canvas App to populate the project database.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant AOAI as Azure AI Services
    participant PG as PostgreSQL

    CA->>PA: Upload MPPR file (IngestMPPRFlow)
    PA->>FA: POST /api/pgvector/ingest-mppr (raw bytes, host key)
    FA->>FA: Parse 5a)NDA MPPR sheet
    FA->>FA: Extract project rows: project_id, period, narrative, raw_content
    FA->>AOAI: embed_batch(raw_content list) — text-embedding-3-large (3072 dims)
    AOAI-->>FA: list of vectors
    FA->>PG: UPSERT nda_projects ON CONFLICT (project_id) DO UPDATE
    PG-->>FA: rows affected
    FA-->>PA: { status: ok, reporting_period, indexed: N }
    PA-->>CA: Show confirmation: "42 projects indexed"
```

`project_id = "{period}|{project_name}"` — used as the upsert key.

---

## 2. EAC Ingest

User uploads the EAC variance Excel. Stored as a Blob; the agent reads it on each validation call.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant BLOB as Azure Blob Storage

    CA->>PA: Upload EAC file (IngestEACFlow)
    PA->>FA: POST /api/pgvector/ingest-eac (raw bytes, host key)
    FA->>BLOB: Upload to nda-data/eac-files/lifecycle_eac_variance.xlsx
    BLOB-->>FA: success
    FA-->>PA: { status: ok, blob_name }
    PA-->>CA: Show confirmation
```

---

## 3. Single Narrative Validation

The primary flow — validates one project narrative and returns a structured agent response.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant AOAI as Azure AI Services
    participant PG as PostgreSQL
    participant BLOB as Azure Blob Storage
    participant AF as Foundry Agent

    CA->>PA: Select project, click Validate (ValidateNarrativeFlow)
    PA->>FA: POST /api/pgvector/validate { project_name, narrative, period, conversation_id: null }
    FA->>AOAI: embed(narrative_text)
    AOAI-->>FA: 3072-dim vector
    FA->>PG: SELECT ... ORDER BY embedding <=> $vector LIMIT 5
    PG-->>FA: top-5 matching project chunks
    FA->>BLOB: guidance_loader — GET guidance/Good_Practice_Guidelines.docx (cached)
    BLOB-->>FA: guidance text
    FA->>BLOB: GET nda-data/eac-files/lifecycle_eac_variance.xlsx
    BLOB-->>FA: EAC data
    FA->>AF: Call agent: system_prompt + context + EAC + narrative
    AF->>AOAI: gpt-5.1-chat completion
    AOAI-->>AF: response text
    AF-->>FA: validation result
    FA->>FA: Generate conversation UUID
    FA->>BLOB: PUT nda-data/conversations/<uuid>.json { messages: [...] }
    FA-->>PA: { conversation_id, is_new_conversation: true, validation_result }
    PA-->>CA: Display validation result
```

On follow-up calls (e.g. "rewrite just the EAC sentence"), the Canvas App passes back `conversation_id` and the agent picks up the prior context from the Blob.

---

## 4. Follow-up Validation (Conversation Continuity)

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant BLOB as Azure Blob Storage
    participant AF as Foundry Agent

    CA->>PA: Send follow-up, include conversation_id (ValidateNarrativeFlow)
    PA->>FA: POST /api/pgvector/validate { narrative, conversation_id: uuid }
    FA->>BLOB: GET nda-data/conversations/<uuid>.json
    BLOB-->>FA: { messages: [prior turn 1, prior turn 2...] }
    FA->>AF: Call agent: system_prompt + conversation history + new narrative
    AF-->>FA: updated validation result
    FA->>BLOB: PUT nda-data/conversations/<uuid>.json (append new messages)
    FA-->>PA: { conversation_id: same uuid, validation_result }
    PA-->>CA: Display updated result
```

---

## 5. Batch Validation (Power Automate)

Validates all narratives in an Excel file in one call. Returns flat CSV rows for easy processing.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant AOAI as Azure AI Services
    participant PG as PostgreSQL
    participant BLOB as Azure Blob Storage
    participant AF as Foundry Agent

    CA->>PA: Upload MPPR, trigger batch (BatchValidateFlow)
    PA->>FA: POST /api/pa-batch-validate (raw bytes, host key)
    FA->>FA: Parse Excel — extract all project rows
    loop For each project
        FA->>AOAI: embed(narrative)
        AOAI-->>FA: vector
        FA->>PG: cosine search top-5
        PG-->>FA: context chunks
        FA->>BLOB: Load EAC + guidance (cached after first iteration)
        FA->>AF: Call agent with narrative + context
        AF-->>FA: validation result
    end
    FA-->>PA: { status: ok, total, passed, warnings, failed, csv_rows: [...] }
    PA-->>CA: Display summary and results table
```

---

## 6. Chat

Conversational assistant — multiple turns about the indexed MPPR data.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App
    participant AOAI as Azure AI Services
    participant PG as PostgreSQL
    participant BLOB as Azure Blob Storage
    participant AF as Foundry Agent

    CA->>PA: Send message (ChatWithNDADataFlow)
    PA->>FA: POST /api/pgvector/chat { question, session_id: null }
    FA->>FA: Generate new session UUID
    FA->>AOAI: embed(question)
    AOAI-->>FA: vector
    FA->>PG: cosine search nda_projects top-5
    PG-->>FA: context chunks
    FA->>AF: Call agent: question + context
    AF->>AOAI: gpt-5.1-chat
    AOAI-->>AF: answer
    AF-->>FA: answer text
    FA->>BLOB: PUT nda-data/conversations/<uuid>.json
    FA-->>PA: { answer, session_id: uuid }
    PA-->>CA: Display answer

    Note over CA,PA: Follow-up turns

    CA->>PA: Send follow-up with session_id
    PA->>FA: POST /api/pgvector/chat { question, session_id: uuid }
    FA->>BLOB: GET nda-data/conversations/<uuid>.json
    BLOB-->>FA: conversation history
    FA->>AF: Call agent: history + question + context
    AF-->>FA: answer
    FA->>BLOB: Append and PUT updated conversation
    FA-->>PA: { answer, session_id: uuid }
    PA-->>CA: Display answer
```

---

## 7. List Projects (Populate Dropdown)

Before validating, the user uploads an Excel to see available project names without ingesting.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant FA as Function App

    CA->>PA: Upload MPPR to populate dropdown (ListProjectsFromSPFlow)
    PA->>FA: POST /api/pgvector/list-projects (raw bytes)
    FA->>FA: Parse 5a)NDA MPPR sheet
    FA->>FA: Extract project names + narratives (no DB write)
    FA-->>PA: { period, projects: [{ project_name, narrative_text }, ...] }
    PA-->>CA: Populate project dropdown
```

---

## 8. SharePoint File Selection (ListSPFilesFlow)

This flow does **not** call the Function App. It uses Power Platform's native SharePoint connector to list files directly.

```mermaid
sequenceDiagram
    participant CA as Canvas App
    participant PA as Power Automate
    participant SP as SharePoint

    CA->>PA: Click Browse SharePoint (ListSPFilesFlow)
    PA->>SP: SharePoint connector — Get files in SentraFileStaging library
    SP-->>PA: file list
    PA-->>CA: Display file picker
    CA->>PA: User selects file → ListProjectsFromSPFlow triggered
    PA->>FA: POST /api/pgvector/list-projects (file bytes from SharePoint)
```