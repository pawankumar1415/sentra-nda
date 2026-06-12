# Code Structure — Custom Approach

Every file has one clear responsibility. Nothing is shared across approaches.

---

## `function_app.py`
Azure Functions v2 entry point. Defines every HTTP route and wires it to the correct handler. Does three things per request: parse the body, call the right module, return JSON. No business logic lives here.

Runs `ensure_schema()` from `db.py` on cold start so the database is always ready before the first request lands.

---

## `auth.py`
Handles everything to do with users and tokens.

- `register_user()` — hashes the password with PBKDF2-HMAC-SHA256 (310k iterations, stdlib only), inserts the user, returns a signed JWT. First user gets `is_admin=True`.
- `login_user()` — checks `is_active`, verifies the password hash, returns a JWT. Returns a clear error if the account is deactivated.
- `require_auth(req)` — called at the top of every protected route. Extracts and verifies the Bearer token. Returns `{ user_id, username, is_admin }`.
- `require_admin(req)` — wraps `require_auth` and additionally checks `is_admin`.
- `list_users()`, `update_user()`, `delete_user()` — admin operations. `delete_user` removes the user and all their data in dependency order.

---

## `db.py`
Manages a module-level `ThreadedConnectionPool` so connections are reused across warm Function invocations. Cold start creates the pool once.

- `ensure_schema()` — creates all tables and the pgvector ivfflat index if they don't exist. Called on every cold start so it is safe to run repeatedly.
- `DBConnection` — context manager that checks out a connection from the pool and returns it on exit.
- `search_projects(embedding, user_id, top_k)` — runs the cosine similarity query against `nda_projects` and returns the top-k matching rows.
- `_get_password()` — detects whether to use a plain password or fetch an Azure AD / Managed Identity token based on the `POSTGRES_USER` and `POSTGRES_PASSWORD` env vars.

---

## `embedder.py`
Thin wrapper around the Azure OpenAI Embeddings API.

- `embed(text)` — returns a single 3072-dim vector for one string.
- `embed_batch(texts)` — embeds a list of strings in one API call. Used by `ingest.py` to embed all project chunks in a single round trip.

The client is created once and reused across warm invocations.

---

## `ingest.py`
Parses the NDA MPPR Excel file and upserts all projects into pgvector.

1. Reads the `5a)NDA MPPR` sheet.
2. Groups rows into data row + narrative row + blank separator.
3. Builds a `raw_content` string per project (name + EAC + narrative combined).
4. Calls `embed_batch()` to embed all projects in one API call.
5. Upserts each row into `nda_projects` using `ON CONFLICT (project_id) DO UPDATE`.

`project_id` = `"{period}|{project_name}"`. This is a shared table — all users write to the same rows. `user_id` is stored as audit metadata.

---

## `ingest_eac.py`
Parses the EAC variance Excel file and stores the results in `nda_eac_variance`.

Applies threshold logic at parse time:
- `< £50k` → flag = `none`
- `£50k–£100k` → flag = `minor`
- `≥ £100k` → flag = `material`
- `≥ £500k` → flag = `major`

EAC data IS per-user — composite PK is `(project_name, user_id)`.

---

## `validate.py`
The core validation pipeline for a single narrative.

Steps:
1. Embed the narrative text.
2. Cosine similarity search `nda_projects` for the top-k most similar chunks.
3. Fetch EAC variance rows for the specific project from `nda_eac_variance`.
4. Load the Good Practice Guidelines text via `guidance_loader.py`.
5. Build a structured prompt containing: the narrative, retrieved project context, EAC data, and guidance rules.
6. Call `gpt-5.1-chat` and parse the response into Layer 1 (formatting) and Layer 2 (data) results.

Returns: `{ layer1, layer2, rewritten_narrative, overall_verdict, _meta }`.

---

## `batch_validate.py`
Loops `validate.py` for every project in an uploaded MPPR Excel file. Parses the file first using the same logic as `ingest.py` to extract project names and narratives, then validates each one and collects results into an array.

---

## `chat.py`
Conversational RAG pipeline.

On each call:
1. Loads the last 20 messages from `conversation.py` (or creates a new session).
2. Embeds the user's question and retrieves relevant project chunks from pgvector.
3. Builds a prompt with the question, retrieved context, and chat history.
4. Calls `gpt-5.1-chat`.
5. Saves both the user message and assistant response to `chat_messages`.

Returns the answer and the session UUID for the frontend to store.

---

## `conversation.py`
CRUD layer for `chat_sessions` and `chat_messages`.

- `create_session(user_id)` — inserts a new session row, returns the UUID.
- `get_history(session_id, limit=20)` — returns the last N messages ordered by creation time.
- `save_message(session_id, role, content)` — inserts one message row.

---

## `sharepoint_client.py`
Connects to SharePoint using an Entra ID app registration (client credentials flow).

- `list_files(site_url, library_name)` — returns a list of Excel files in a SharePoint document library.
- `download_file(file_url)` — downloads a file and returns the bytes. Used by the `/api/sharepoint/list-projects` route to extract the project list from a SharePoint-hosted MPPR file.

---

## `guidance_loader.py`
Fetches the Good Practice Guidelines DOCX from Azure Blob Storage (`guidance` container) and returns the text. Result is cached in memory for the lifetime of the Function instance so Blob is only called once per cold start.

---

## `host.json`
Azure Functions host configuration — sets Python worker count, logging levels, and retry policy.

---

## `requirements.txt`
Python dependencies. Key packages:
- `azure-functions` — Functions runtime
- `psycopg2-binary` — PostgreSQL driver
- `pgvector` — pgvector psycopg2 adapter
- `openai` — Azure OpenAI SDK
- `PyJWT` — JWT sign/verify
- `azure-identity` — Managed Identity / Entra ID credential
- `azure-storage-blob` — Blob Storage client