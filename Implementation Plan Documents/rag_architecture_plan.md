# NDA Narrative Validation — Open-Source RAG Architecture Plan

## Overview

Replace Azure AI Search + Azure AI Foundry Agent with a self-contained
RAG pipeline using Azure OpenAI embeddings, Azure PostgreSQL + pgvector,
and a two-route Azure Function API. The existing Excel extraction logic
and EAC variance tools are reused unchanged.

---

## System Architecture

```mermaid
graph TB
    subgraph Client["Client / Caller"]
        U1["Excel File Upload"]
        U2["Validate Narrative Request"]
    end

    subgraph AzureFunction["Azure Function App (Python v2)"]
        R1["POST /api/ingest\nIngest Route"]
        R2["POST /api/validate\nValidation Route"]

        subgraph IngestPipeline["Ingest Pipeline"]
            I1["Parse Excel\n5a NDA MPPR sheet\nColumns A-AK"]
            I2["Clean & Chunk\nOne doc per project row"]
            I3["Embed via\nAzure OpenAI\ntext-embedding-3-small\n1536 dims"]
            I4["Upsert to PGVector\nmergeOrUpload by project_id"]
        end

        subgraph ValidatePipeline["Validation Pipeline"]
            V1["Embed user query\nAzure OpenAI"]
            V2["Vector Search\nPGVector cosine similarity\ntop-k=5"]
            V3["Check EAC Variance\nload lifecycle_eac_variance.xlsx\nthreshold logic"]
            V4["Build Augmented Prompt\nsystem prompt + retrieved chunks\n+ EAC data"]
            V5["Call Azure OpenAI GPT\ngpt-5.1-chat\nstructured output"]
            V6["Return JSON Response\nLayer 1 + Layer 2 results"]
        end
    end

    subgraph AzureOpenAI["Azure OpenAI (existing deployment)"]
        AOI["text-embedding-3-small\nEmbedding endpoint"]
        AOG["GPT deployment\nChat completion endpoint"]
    end

    subgraph PGVector["Azure PostgreSQL Flexible Server"]
        PG["PostgreSQL + pgvector extension\nnda_projects table\nvector col: embedding 1536"]
    end

    U1 -->|"multipart/form-data\nor JSON body"| R1
    U2 -->|"JSON: narrative + project_name"| R2

    R1 --> I1 --> I2 --> I3 --> I4
    I3 <-->|embed request| AOI
    I4 <-->|INSERT / UPDATE| PG

    R2 --> V1
    V1 <-->|embed request| AOI
    V1 --> V2
    V2 <-->|cosine search| PG
    V2 --> V3
    V3 --> V4
    V4 --> V5
    V5 <-->|chat completion| AOG
    V5 --> V6
```

---

## Database Schema

```mermaid
erDiagram
    NDA_PROJECTS {
        TEXT    project_id          PK "Unique: period + project code"
        TEXT    project_name
        TEXT    period_short_name
        TEXT    rag_status
        TEXT    dca_rag_status
        FLOAT   eac_total
        FLOAT   eac_variance
        INTEGER schedule_variance_days
        TEXT    narrative_text
        TEXT    raw_content         "Full concatenated row text for retrieval"
        VECTOR  embedding           "1536-dim from text-embedding-3-small"
        TIMESTAMPTZ indexed_at
    }
```

---

## Process Flows

### Ingest Flow — POST /api/ingest

```mermaid
sequenceDiagram
    participant Client
    participant IngestFn as Azure Function /ingest
    participant Parser as Excel Parser
    participant OAI as Azure OpenAI Embeddings
    participant PG as PostgreSQL + pgvector

    Client->>IngestFn: POST /api/ingest {file: P07.xlsx}
    IngestFn->>Parser: parse_mppr_sheet(file)
    Parser-->>IngestFn: List[ProjectRow] (cleaned dicts)

    loop For each project row
        IngestFn->>IngestFn: build_text_chunk(row)\ncombine all fields into searchable string
        IngestFn->>OAI: POST /embeddings {input: chunk_text, model: text-embedding-3-small}
        OAI-->>IngestFn: float[1536] vector
        IngestFn->>PG: INSERT INTO nda_projects ... ON CONFLICT (project_id) DO UPDATE
    end

    IngestFn-->>Client: {status: "ok", indexed: N, period: "P07"}
```

### Validation Flow — POST /api/validate

```mermaid
sequenceDiagram
    participant Client
    participant ValFn as Azure Function /validate
    participant OAI as Azure OpenAI Embeddings
    participant PG as PostgreSQL + pgvector
    participant XLSX as EAC Variance XLSX
    participant GPT as Azure OpenAI GPT

    Client->>ValFn: POST /api/validate\n{narrative, project_name, period}

    ValFn->>OAI: embed(narrative)
    OAI-->>ValFn: query_vector[1536]

    ValFn->>PG: SELECT ... ORDER BY embedding <=> query_vector LIMIT 5
    PG-->>ValFn: top-5 matching project chunks

    ValFn->>XLSX: check_eac_variance(project_name, period)
    XLSX-->>ValFn: {eac_variance, schedule_days, threshold_flag}

    ValFn->>ValFn: build_prompt(\n  system_prompt,\n  retrieved_chunks,\n  eac_data,\n  narrative\n)

    ValFn->>GPT: POST /chat/completions {messages, response_format}
    GPT-->>ValFn: structured validation response

    ValFn-->>Client: {\n  layer1: {compliance_score, issues[]},\n  layer2: {eac_explained, schedule_explained},\n  suggestions: []\n}
```

---

## Folder Structure (proposed changes)

```
Custom Solution/
├── azure_function/
│   ├── function_app.py          ← ADD /ingest + /validate routes
│   ├── ingest.py                ← NEW: Excel parse → embed → PGVector upsert
│   ├── validate.py              ← NEW: query embed → vector search → GPT call
│   ├── db.py                    ← NEW: PostgreSQL connection pool + pgvector helpers
│   ├── embedder.py              ← NEW: Azure OpenAI embedding wrapper
│   ├── requirements.txt         ← ADD: psycopg2-binary, pgvector, openai
│   └── local.settings.json      ← ADD: POSTGRES_*, AZURE_OPENAI_* vars
│
├── agent/                       ← KEEP as-is (Foundry agent, used when perms fixed)
├── NDA Data/                    ← KEEP (EAC xlsx file read by validate.py)
└── README.md                    ← UPDATE with new architecture
```

---

## New Environment Variables

| Variable | Used in | Description |
|---|---|---|
| `POSTGRES_HOST` | db.py | Azure PostgreSQL server hostname |
| `POSTGRES_DB` | db.py | Database name |
| `POSTGRES_USER` | db.py | DB username |
| `POSTGRES_PASSWORD` | db.py | DB password |
| `POSTGRES_PORT` | db.py | Default: 5432 |
| `AZURE_OPENAI_ENDPOINT` | embedder.py | Your Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | embedder.py | Azure OpenAI API key |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | embedder.py | e.g. `text-embedding-3-small` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | validate.py | e.g. `gpt-5.1-chat` |

---

## Implementation Phases

### Phase 1 — Database + Embedding Setup
- Provision Azure PostgreSQL Flexible Server
- Enable `pgvector` extension: `CREATE EXTENSION vector;`
- Create `nda_projects` table with `embedding vector(1536)` column
- Create `ivfflat` index on embedding column for fast cosine search
- Write `db.py` (connection pool) and `embedder.py` (OpenAI wrapper)

### Phase 2 — Ingest Route
- Write `ingest.py`: reuse existing Excel parser → build text chunks → embed → upsert
- Add `POST /api/ingest` route to [function_app.py](file:///C:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/azure_function/function_app.py)
- Test: upload P07 → verify rows in pg with `SELECT count(*) FROM nda_projects`

### Phase 3 — Validate Route
- Write `validate.py`: embed query → pgvector cosine search → EAC check → GPT call
- Structured output format matches current agent response (Layer 1 + Layer 2)
- Add `POST /api/validate` route to [function_app.py](file:///C:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/azure_function/function_app.py)
- Test: send a known narrative → verify correct project retrieved + EAC flag applies

### Phase 4 — Deploy & Wire Up
- Deploy updated Azure Function
- Update [local.settings.json](file:///C:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/agent/local.settings.json) / App Settings with Postgres + OpenAI vars
- Optionally retire the Azure AI Foundry Agent route once this is stable

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Embedding model | `text-embedding-3-small` (Azure OpenAI) | Already deployed, 1536 dims, fast, no cold start |
| Vector DB | Azure PostgreSQL + pgvector | Managed, no file persistence issues, SQL metadata filtering |
| Distance metric | Cosine similarity (`<=>` operator) | Best for text embeddings |
| Index type | `ivfflat` (lists=100) | Good balance of speed vs recall for ~1000 rows |
| Chunk strategy | One row = one document | Each project row is self-contained |
| GPT call | Direct `openai` SDK (Azure endpoint) | No LangChain overhead, simpler prompt control |
| EAC check | Same [tools.py](file:///C:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/agent/tools.py) functions | Zero code change, already tested |
