# NDA Narrative Validation System

An AI-powered tool that validates NDA project reporting narratives against writing guidelines and live data movements, surfaced via Microsoft Teams.

---

## How It All Works — The Dual Architecture Approach

We are current exploring a two-pronged approach to find the best balance of flexibility and performance.

### Approach 1: Custom RAG API Pipeline (`rag_function/`)
A fully-custom, open-source backend using Azure Functions, PostgreSQL (pgvector), and the OpenAI Python SDK.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DATA INGESTION (Triggered via Power Automate / SharePoint)                 │
│                                                                             │
│  1. Upload P07 Excel File ──► POST /api/ingest ─────┐                       │
│  2. Upload EAC Excel File ──► POST /api/ingest-eac ─┴─► PostgreSQL Database │
│                                                         (pgvector)          │
│                                                                             │
│  VALIDATION (Triggered by user/UI)                                          │
│                                                                             │
│  User / Client App ──► POST /api/validate ──► Query PostgreSQL Context      │
│                                           ──► Evaluate via Azure OpenAI     │
│                                           ──► Return JSON Verdict           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Approach 2: Azure AI Foundry Agent (`agent/`)
An AI-orchestrator approach using the Azure AI Agents SDK, where the LLM is given tools to autonomously fetch data.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Validation Request ──► Azure AI Agent ──► Decides which tools to run       │
│                                        ──► Calls RAG API for EAC Database   │
│                                        ──► Builds contextual understanding  │
│                                        ──► Applies Good Practice Guidelines │
│                                        ──► Returns Validation Results       │
└─────────────────────────────────────────────────────────────────────────────┘
```

*(Ultimately, Phase 3 will wrap the chosen approach in an Azure Bot Service to expose it to Microsoft Teams).*

---

## Folder Structure Explained

```
Custom Solution/
│
├── Implementation Plan Documents/   ← Architecture docs and this README
├── NDA Data/                        ← Sample Data (gitignored)
├── test_rag.py                      ← End-to-end local test suite for RAG API
├── debug_excel.py                   ← Excel parsing debug utility
├── setup_db.py                      ← PostgreSQL Schema setup script
│
├── rag_function/                    ← COMPONENT 1: Custom RAG API Pipeline
│   ├── function_app.py              ← HTTP triggers: /ingest, /ingest-eac, /validate
│   ├── db.py                        ← PostgreSQL connection pooling
│   ├── embedder.py                  ← Azure OpenAI Embeddings implementation
│   ├── ingest.py                    | Excel extractors & database upserts
│   ├── ingest_eac.py                |
│   ├── validate.py                  ← RAG + Validation logic orchestrator
│   ├── local.settings.json          ← Credentials for local dev (gitignored)
│   └── requirements.txt             ← Python dependencies
│
└── agent/                           ← COMPONENT 2: Azure AI Foundry Agent
    ├── agent_runner.py              ← Main orchestrator logic
    ├── tools.py                     ← Python tools (functions the agent can call)
    ├── system_prompt.py             ← Good Practice guidelines
    ├── config.py                    ← Thresholds and Agent config
    ├── local.settings.json          ← Credentials for local dev (gitignored)
    └── requirements.txt             ← Python dependencies
```

---

## Getting Started: The Custom RAG API Pipeline

### 1. Set Up the Database
This solution requires an **Azure Database for PostgreSQL Flexible Server** with the `pgvector` extension enabled.

1. Ensure your connection strings are in `rag_function/local.settings.json`:
   - `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
2. Run the setup script to create the schema and vector indexes:
   ```powershell
   venv\Scripts\python setup_db.py
   ```

### 2. Run Local Tests
The test suite will check database connectivity, run the Azure OpenAI embedder, index local Excel files from the `NDA Data/` folder, and test narrative validation.

```powershell
venv\Scripts\python test_rag.py
```

### 3. Deploy the API to Azure Functions
1. CD into the function directory:
   ```powershell
   cd rag_function
   ```
2. Publish to your Azure Function App (e.g., `nda-python-backend`):
   ```powershell
   func azure functionapp publish <YOUR_FUNCTION_APP_NAME>
   ```
3. Sync your local settings to the cloud:
   ```powershell
   func azure functionapp publish <YOUR_FUNCTION_APP_NAME> --publish-settings-only
   ```

---

## Getting Started: Azure AI Foundry Agent

The agent requires an **Azure AI Foundry Project**, a connected **Azure OpenAI** model deployment, and (optionally) an **Azure AI Search** connection. 

Currently, the Agent authenticates using the developer's Entra ID (`az login`) fallback. 

1. Ensure your credentials are in `agent/local.settings.json`.
2. Install the necessary SDKs:
   ```powershell
   venv\Scripts\pip install azure-ai-projects azure-ai-agents azure-identity
   ```
3. Run or debug the agent logic using:
   ```powershell
   venv\Scripts\python debug_agent_run.py
   ```

*(Note: For autonomous cloud execution, the Service Principal mapped to the app will require the `Azure AI Developer` and `Search Index Data Reader` role assignments).*

---

## The Two-Layer Validation Explained

Both approaches share the same fundamental goal: validating raw periodic project narratives.

### Layer 1 — Guidance & Structure
The AI checks the narrative against the **Good Practice Reference** guidelines:
- Is it written as flowing prose (not bullet points)?
- Are all acronyms natively expanded on first use?
- Are all dates written in full?
- Are figures consistent with reported data?

### Layer 2 — Data-Driven Movement Check
The system cross-references the submitted text with historical baseline tracking (`lifecycle_eac_variance`).

| EAC Movement | Action Required |
|---|---|
| < £50k | No comment required |
| ≥ £0.1m (one decimal place) | Must be explicitly referenced |
| ≥ £0.5m | Requires detailed rationale |
| Any schedule slippage | Must be mentioned and explained |
