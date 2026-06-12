# Code Structure — Foundry Approach

---

## `function_app.py`
Azure Functions v2 entry point. Defines every HTTP route and dispatches to the correct handler. No business logic lives here.

Contains two sets of routes:
- **Active pgvector routes** (`/api/pgvector/*`) — what all 12 Power Automate flows call. These delegate to `pgvector_backend.py`.
- **Legacy routes** (`/api/validate`, `/api/chat`, etc.) — AI Search backed, still present in the code but not called by current flows.
- **`/api/pa-batch-validate`** — special Power Automate batch endpoint that does not follow the `/pgvector/` path convention but internally calls `pa_batch_validate_pgvector()`.

---

## `pgvector_backend.py`
The active backend for all Power Automate flows. Handles ingest, validate, chat, batch, search, list, and history using pgvector for retrieval and the Foundry agent for generation.

Key functions:
- `validate_narrative_pgvector()` — embed narrative → cosine search → load EAC + guidance → call Foundry agent → return response
- `chat_pgvector()` — load conversation blob → embed question → cosine search → call Foundry agent → save conversation blob
- `ingest_mppr_pgvector()` — parse Excel → embed all projects → upsert `nda_projects`
- `pa_batch_validate_pgvector()` — parse Excel → loop `validate_narrative_pgvector()` per project
- `list_projects_pgvector()` — parse Excel without DB write, return project names
- `get_history_pgvector()` — read conversation blob and return message history

---

## `agent_runner.py`
Manages the connection to the Azure AI Foundry agent and handles conversation memory.

- Connects to the Foundry project via `AIProjectClient` using Managed Identity.
- Sends the agent a structured input list: system prompt + conversation history + current turn.
- Saves the updated conversation to a Blob JSON file (`nda-data/conversations/<uuid>.json`) after each call.
- Falls back to Chat Completions API if the Responses API is unavailable.
- Non-fatal blob write: if saving the conversation fails, the validation result is still returned.

---

## `batch_validate.py`
Loops `validate_narrative_pgvector()` for every project in an uploaded Excel file. Parses the MPPR sheet, extracts narratives, and runs validation on each one sequentially.

---

## `chat.py`
Chat wrapper for the Foundry agent. Loads the conversation blob for an existing session (or creates a new one), embeds the question, retrieves pgvector context, calls the agent, and saves the updated blob.

---

## `tools.py`
Python functions that the Foundry agent can call as tools during a conversation. Examples:
- EAC variance lookup for a specific project
- Schedule movement calculation
- RAG status history retrieval

These are registered with the agent via the Foundry portal or `create_agent.py`.

---

## `system_prompt.py`
Contains the agent system prompt as a Python string. Defines the agent's persona, validation rules (Layer 1 and Layer 2), output format requirements, and how to interpret EAC and schedule data.

Imported by `agent_runner.py` and injected as the first system message in every conversation.

---

## `ingest_helper.py`
Legacy module for Azure AI Search indexing. Handles uploading documents to the AI Search index and running keyword/full-text searches. Not called by any current flows — all active flows use pgvector via `pgvector_backend.py`.

---

## `guidance_loader.py`
Fetches the Good Practice Guidelines DOCX from the `guidance` Azure Blob container and extracts the text. Result is cached in a module-level variable so Blob Storage is only called once per cold start.

---

## `config.py`
Single source of truth for all environment variable reads. Every other module imports constants from here rather than calling `os.environ` directly. Makes it easy to see all required configuration in one place.

Key constants:
- `PROJECT_ENDPOINT` — AI Foundry project URL
- `MODEL_DEPLOYMENT_NAME` — chat model name
- `AZURE_STORAGE_ACCOUNT_URL` — Blob Storage URL
- `AZURE_STORAGE_CONTAINER_NAME` — container name for EAC + conversation blobs

---

## `create_agent.py`
One-time script to create the Foundry agent and register tools. Not part of the runtime — run once when setting up a new environment. After running, the agent ID is stored in the Foundry portal and referenced via `PROJECT_ENDPOINT`.

---

## `setup_memory.py`
CLI tool for managing the Foundry Memory Store (a preview feature). Not used in the current production deployment — conversation history is handled via Blob Storage blobs instead.

---

## `host.json`
Azure Functions host configuration — logging levels, worker count, retry policy.

---

## `requirements.txt`
Python dependencies. Key packages:
- `azure-functions` — Functions runtime
- `azure-ai-projects` — AI Foundry project client
- `azure-identity` — Managed Identity credential
- `azure-storage-blob` — Blob Storage client
- `psycopg2-binary` — PostgreSQL driver
- `pgvector` — pgvector psycopg2 adapter
- `openai` — Azure OpenAI SDK (for embeddings)