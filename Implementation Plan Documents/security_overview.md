# Security Overview — NDA Narrative Validation System

**Project:** Sentra  
**Resource Group:** sellafield-dpmo-dev  
**Last Updated:** April 2026

---

## What We Have Built

We have two separate approaches running in the same Azure resource group:

| Approach | Frontend | Backend Function App | Who Uses It |
|---|---|---|---|
| Custom RAG | React (Azure Static Web App) | nda-python-backend | All company analysts, shared data |
| Foundry Agent | Power Apps | nda-foundry-api | All company staff, shared data |

Both approaches use shared data — any user who uploads an MPPR file is updating the same dataset that everyone else reads from. The difference is the interface and the underlying technology stack, not the data access model.

---

## How Users Log In

### Custom RAG (React)

Users create an account with a username and password. When they log in, the backend gives them a **JWT token** — think of it as a temporary pass that lasts 8 hours. Every request from the React app sends this token in the header. If it is missing, expired or wrong, the backend refuses the request straight away before touching any data.

There are two types of users — standard and admin. The first person who registers is automatically made admin. Admins can see and manage all user accounts. Standard users can use all features but cannot manage other users' accounts. This is enforced on the backend, not just in the UI.

### Foundry Agent (Power Apps)

Power Apps is inside Microsoft 365. Anyone who can open the app is already a verified company employee through Entra ID (company SSO). We do not need a separate login for this.

The API itself is protected by an Azure **Function Host Key** — a long secret key that sits inside the Power Automate flow. End users never see it. Anyone calling the API without this key gets a 401 back.

---

## Backend Security

### Custom RAG Backend

**Every single endpoint is protected.** The first thing every route does is call `require_auth(req)` which reads the JWT from the Authorization header, validates it and returns the user. If anything is wrong with the token it throws a `PermissionError` and returns 401 immediately. Nothing else runs.

**Password hashing.** We use PBKDF2-HMAC-SHA256 with 310,000 iterations and a random salt. Passwords are never stored in plain text anywhere. Even if someone got access to the database, they could not recover the original passwords.

**Shared data model.** MPPR project data and EAC variance data are stored once in PostgreSQL and shared across all users. The `user_id` column is kept on the data tables as an audit trail (so we know who uploaded what and when) but it is not used to filter what any user can read. Everyone sees the same portfolio data.

**Admin protection.** Admin-only endpoints use `require_admin(req)` which first validates the JWT and then checks the `is_admin` flag. An admin cannot deactivate or delete their own account — this is enforced in the backend code so it cannot be bypassed from the UI.

**Input validation.** Usernames must be at least 3 characters, passwords at least 8. Duplicate usernames are caught and returned as a proper error. We use parameterised queries everywhere so SQL injection is not possible. File uploads only accept `.xlsx` — any other format is rejected before processing.

### Foundry Agent Backend

**No user accounts or JWTs.** The only gate is the Function Host Key on the URL. Access control above that is handled entirely by Microsoft 365 — if someone cannot log in to the company's Microsoft account, they cannot open Power Apps, so they never reach the API.

**No credentials in code.** The backend uses Managed Identity for all Azure services — AI Foundry, AI Search, Blob Storage. No connection strings or API keys are stored in the code or in version control. The only exception is `AZURE_OPENAI_API_KEY` which lives in Azure Function App Settings as an encrypted environment variable.

---

## Azure Resources

### Foundry Agent (nda-foundry-api)

| Resource | Azure Service | Detail |
|---|---|---|
| AI Orchestration | Azure AI Foundry Project | movar-secure-azure (on movar-secure-azure-resource) |
| GPT Model | Azure AI Foundry | Deployment: gpt-5.1-chat on movar-secure-azure-resource |
| Backend API | Azure Functions (Python) | nda-foundry-api — /api/validate, /api/ingest-mppr, /api/ingest-eac, /api/chat |
| Knowledge Index | Azure AI Search | movar-nda-aisearch — index: nda-mppr-projects (17 fields: project metadata + narrative text) |
| EAC Variance Data | Azure Blob Storage | ndadatastorage — container: nda-data — blob: lifecycle_eac_variance.xlsx |
| Conversation History | Azure Blob Storage | ndadatastorage — container: nda-data — one JSON file per session UUID |
| File Transport | Power Automate | Triggered on SharePoint file creation, HTTP multipart POST to Azure Function |
| User Interface | Power Apps Canvas App | AI Chat & Document UI |

### Custom RAG (nda-python-backend)

| Resource | Azure Service | Detail |
|---|---|---|
| Backend API | Azure Functions (Python) | nda-python-backend — /api/ingest, /api/ingest-eac, /api/validate, /api/chat, /api/list-projects, /api/batch-validate |
| Vector Database | Azure PostgreSQL Flexible Server | DB: nda_agent — nda_projects (pgvector 3072-dim ivfflat cosine index), nda_eac_variance, users, chat_sessions, chat_messages |
| Embedding Model | Azure OpenAI | text-embedding-3-large (3072 dimensions) |
| GPT Model | Azure OpenAI | Deployment: gpt-5.1-chat |
| User Interface | Azure Static Web App | nda-custom-frontend-static — React 19 + TypeScript + Vite |

---

## Azure Permissions (Who Can Access What)

Both Function Apps run with **System-Assigned Managed Identities**. We gave each identity only what it needs and nothing more.

### Foundry Agent (nda-foundry-api) — RBAC Roles

| Role | Scope |
|---|---|
| Azure AI Developer | Resource Group + movar-secure-azure-resource |
| Cognitive Services OpenAI User | movar-secure-azure-resource |
| Search Index Data Contributor | movar-nda-aisearch |
| Storage Blob Data Contributor | ndadatastorage |

### Custom RAG (nda-python-backend) — RBAC Roles

| Role | Scope |
|---|---|
| Cognitive Services OpenAI User | Azure OpenAI resource (for embeddings + GPT) |
| PostgreSQL access | Managed Identity token via `https://ossrdbms-aad.database.windows.net` — no password stored |

Custom RAG does not use Azure AI Search. All vector search is done inside PostgreSQL using the pgvector extension directly.

If a Function App were ever compromised, it can only touch the specific services listed above — nothing else in the subscription.

---

## Data in Transit

Everything goes over HTTPS. There is no unencrypted HTTP anywhere.

- Browser to React app — HTTPS, enforced by Azure Static Web Apps
- React app to backend API — HTTPS only
- Power Apps to Power Automate to API — HTTPS, enforced by Power Platform
- Backend to Azure OpenAI, AI Search, Blob Storage — HTTPS, enforced by Azure SDKs
- Backend to PostgreSQL — TLS required (`sslmode=require` in connection string)

---

## Data at Rest

All Azure storage services encrypt everything at rest with AES-256 automatically. We do not need to configure this — Azure does it by default.

| What | Where | Encrypted |
|---|---|---|
| User accounts, MPPR narratives, EAC data, chat history | Azure PostgreSQL | Yes — AES-256 |
| EAC variance file, conversation history (Foundry), guidance DOCX | Azure Blob Storage | Yes — AES-256 |
| Indexed MPPR project data | Azure AI Search | Yes — AES-256 |
| JWT secret, API keys | Azure Function App Settings | Yes — encrypted at rest |

---

## How Data is Handled

### Custom RAG

Any authenticated user can upload MPPR and EAC files. When they do, that data is written into the shared PostgreSQL tables and immediately available to all other users. The `user_id` is recorded on each row as an audit trail so we can see who uploaded what, but it does not restrict who can read the data.

Chat history is stored in PostgreSQL per session. Each session has a UUID that the browser keeps in localStorage. History survives page refreshes and powers follow-up questions.

When a narrative is sent for validation, the text goes to Azure OpenAI for processing. Microsoft does not store this beyond the API call under the Azure Data Processing Agreement.

When a user account is deleted by an admin, their user record and chat sessions are removed. The MPPR and EAC data they uploaded stays in place because it is shared — deleting it would affect everyone.

### Foundry Agent

There is no per-user data here either. All MPPR data goes into one shared AI Search index. When anyone uploads a new MPPR file via Power Automate, all users immediately see the updated data.

Chat history is stored in Azure Blob Storage as a JSON file per conversation session, identified by a UUID stored in the Power Apps user's browser. There is no link between a session UUID and a specific person's identity.

---

## Frontend Security

### React App

- Hosted on Azure Static Web Apps — only static files, no server process exposed
- JWT token is kept in browser memory for the session, not in localStorage
- CORS is configured on the Function App to only allow requests from the Static Web App domain (`https://lemon-bay-04878fd03.4.azurestaticapps.net`)
- The Azure Function Key is bundled in the built app — this is a known limitation of static frontends, but the JWT is the real security gate
- File uploads are validated to `.xlsx` only — any other file type is rejected in the UI before the request is sent

### Power Apps

- Gated by Microsoft Entra ID — only company employees with a valid Microsoft 365 account can open it
- The Function Host Key is inside the Power Automate connector, never visible to the user in the app
- No data is cached on the user's device — everything is fetched fresh from the API

---

## Things to Keep on Top Of

| Task | How Often | Why |
|---|---|---|
| Rotate JWT_SECRET | Once a year or when someone leaves | All existing sessions are immediately invalidated |
| Rotate Azure OpenAI API key | Per company security policy | Update in Function App settings (nda-python-backend) after rotation |
| Review admin users in React app | Every quarter | Deactivate accounts for people who have left |
| Check AI Search index document count after each ingest | After every MPPR upload | Make sure all projects were indexed, not silently skipped |
| Check PostgreSQL nda_projects row count after each ingest | After every MPPR upload via Custom RAG | Same check for the pgvector side |