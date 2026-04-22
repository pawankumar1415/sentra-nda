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

## Layered Security Model

Security is not dependent on any single layer. Each layer adds its own control so that a failure in one does not expose everything else.

| Layer | Control | How Access is Restricted |
|---|---|---|
| Identity | Entra ID / M365 sign-in (Foundry) or JWT (Custom RAG) | Only authenticated users can reach any functionality |
| Application | Power Apps sharing + JWT validation | Only approved users or groups can open the app and call protected endpoints |
| Workflow | Power Automate connection references and flow permissions | Only app-triggered paths can run flows — users cannot call the API directly |
| Data | PostgreSQL parameterised queries / AI Search index | No raw data access — everything goes through the API layer |
| API | Function Host Key (Foundry) / JWT (Custom RAG) | Unauthenticated requests are rejected before any code runs |
| Monitoring | Azure Function logs, PostgreSQL audit, flow run history | All activity is traceable |

---

## Backend Security

### Custom RAG Backend

**Every single endpoint is protected.** The first thing every route does is call `require_auth(req)` which reads the JWT from the Authorization header, validates it and returns the user. If anything is wrong with the token it throws a `PermissionError` and returns 401 immediately. Nothing else runs.

**Password hashing.** We use PBKDF2-HMAC-SHA256 with 310,000 iterations and a random salt. Passwords are never stored in plain text anywhere. Even if someone got access to the database, they could not recover the original passwords.

**Shared data model.** MPPR project data and EAC variance data are stored once in PostgreSQL and shared across all users. The `user_id` column is kept on the data tables as an audit trail (so we know who uploaded what and when) but it is not used to filter what any user can read. Everyone sees the same portfolio data.

**Admin protection.** Admin-only endpoints use `require_admin(req)` which first validates the JWT and then checks the `is_admin` flag. An admin cannot deactivate or delete their own account — this is enforced in the backend code so it cannot be bypassed from the UI.

**Input validation.** Usernames must be at least 3 characters, passwords at least 8. Duplicate usernames are caught and returned as a proper error. We use parameterised queries everywhere so SQL injection is not possible.

**File upload validation.** The React frontend rejects any file that is not `.xlsx` before the upload request is even sent. This is a UX-level gate — if someone calls the API directly with a non-Excel file, the backend will throw a parse error. A proper MIME-type check at the backend level should be added if the API is ever exposed more broadly.

### Foundry Agent Backend

**No user accounts or JWTs.** The only gate is the Function Host Key on the URL. Access control above that is handled entirely by Microsoft 365 — if someone cannot log in to the company's Microsoft account, they cannot open Power Apps, so they never reach the API.

**No credentials in code.** The backend uses Managed Identity for all Azure services — AI Foundry, AI Search, Blob Storage. No connection strings or API keys are stored in the code or in version control. The Azure Function App Settings store any environment-specific configuration as encrypted variables.

**Inputs are not passed directly to the model.** The Canvas app calls a Power Automate flow, the flow calls the Azure Function, and the Function assembles the prompt. The user's question goes through our own prompt-building code before it reaches OpenAI — we do not pass raw user input directly to the model endpoint.

---

## Power Apps and Canvas App Security

The Foundry Agent is delivered through a Power Apps Canvas app. Security here works in two levels.

### Platform-level access

The app is shared only with approved Entra ID security groups. We do not share it with all employees or entire departments. If a user does not belong to an approved group, Power Apps will not let them open the app at all — they never reach any screen.

### In-app authorisation

At app start, the Canvas app checks whether the current user is a member of the approved access group. If they are not, the app shows an access denied screen and blocks all navigation. This is a second check on top of the platform-level sharing.

### SharePoint document security

MPPR files are uploaded to a dedicated SharePoint document library, not a general shared location. The library is only accessible to approved user groups and the Power Automate service account. The key controls we have in place:

- Files uploaded through the app go into the controlled library automatically
- The library uses the SharePoint permission model — users cannot access files unless they have been granted access at the library or group level
- Broad anonymous and organisation-wide sharing links are disabled on this library
- Versioning is enabled so previous uploads are not silently overwritten

The service account used by Power Automate has the minimum permissions needed — it can read and write to the MPPR library only, not to the whole SharePoint site.

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
| Backend API | Azure Functions (Python) | nda-python-backend — /api/ingest, /api/ingest-eac, /api/validate, /api/chat, /api/list-projects, /api/batch-validate, /api/sharepoint/files, /api/sharepoint/list-projects |
| Vector Database | Azure PostgreSQL Flexible Server | DB: nda_agent — nda_projects (pgvector 3072-dim ivfflat cosine index), nda_eac_variance, users, chat_sessions, chat_messages |
| Embedding Model | Azure OpenAI | text-embedding-3-large (3072 dimensions) — authenticated via API key |
| GPT Model | Azure OpenAI | Deployment: gpt-5.1-chat — authenticated via API key |
| User Interface | Azure Static Web App | nda-custom-frontend-static — React 19 + TypeScript + Vite |

Custom RAG does **not** use Azure AI Search. All vector search runs inside PostgreSQL using the pgvector extension directly.

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

Authentication to Azure OpenAI goes through the Azure AI Developer role via Managed Identity — no API key is needed or stored.

### Custom RAG (nda-python-backend) — RBAC Roles

| Role | Scope |
|---|---|
| PostgreSQL access | Managed Identity token via `https://ossrdbms-aad.database.windows.net` — no password stored |

Azure OpenAI (both GPT and embeddings) is accessed via `AZURE_OPENAI_API_KEY` stored in Function App Settings as an encrypted environment variable. There is no Managed Identity RBAC role for OpenAI on this Function App — it uses the API key directly.

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
| MPPR source files | SharePoint document library | Yes — Microsoft 365 encryption |

---

## How Data is Handled

### Custom RAG

Any authenticated user can upload MPPR and EAC files. When they do, that data is written into the shared PostgreSQL tables and immediately available to all other users. The `user_id` is recorded on each row as an audit trail so we can see who uploaded what, but it does not restrict who can read the data.

Chat history is stored in PostgreSQL per session. Each session has a UUID that the browser keeps in localStorage. History survives page refreshes and powers follow-up questions.

When a narrative is sent for validation, the text goes to Azure OpenAI for processing. Microsoft does not store this beyond the API call under the Azure Data Processing Agreement.

When a user account is deleted by an admin, their user record and chat sessions are removed — which cascade-deletes all their stored conversation messages too. The MPPR and EAC data they uploaded stays in place because it is shared — deleting it would affect everyone.

### Foundry Agent

There is no per-user data here either. All MPPR data goes into one shared AI Search index. When anyone uploads a new MPPR file via Power Automate, all users immediately see the updated data.

Chat history is stored in Azure Blob Storage as a JSON file per conversation session, identified by a UUID the Power Apps app keeps in the user's browser. There is no link between a session UUID and a specific person's identity.

---

## Frontend Security

### React App

- Hosted on Azure Static Web Apps — only static files, no server process exposed
- JWT token is kept in browser memory for the session, not in localStorage
- CORS is configured on the Function App to only allow requests from the Static Web App domain (`https://lemon-bay-04878fd03.4.azurestaticapps.net`)
- The Azure Function Key is bundled in the built app — this is a known limitation of static frontends, but the JWT is the real security gate
- File uploads check for `.xlsx` extension in the onChange handler before the request is sent — any other file type shows an error and is not uploaded

### Power Apps

- Gated by Microsoft Entra ID — only company employees with a valid Microsoft 365 account can open it
- App is shared with specific Entra ID security groups, not the whole organisation
- In-app group membership check at startup blocks navigation if the user is not in an approved group
- The Function Host Key is inside the Power Automate connector, never visible to the user in the app
- No data is cached on the user's device — everything is fetched fresh from the API
- All API calls go through Power Automate, not directly from the Canvas app — credentials and keys never touch the frontend

---

## Key Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| App shared too broadly in Power Apps | Unauthorised users can open the app | Share only with approved Entra ID groups and validate group membership at app start |
| SharePoint library open to wider audience | MPPR files accessible outside intended audience | Dedicated library with restricted membership, no organisation-wide sharing links |
| Flow credentials embedded insecurely | Credential leakage or unauthorised API use | Managed Identity used wherever possible; API keys stored only in encrypted Function App Settings |
| Someone calls the backend API directly, bypassing the frontend file check | Non-Excel files submitted to the backend | Backend throws a parse error today — a MIME-type check at the API level should be added for defence in depth |
| JWT_SECRET rotated without notice | All users logged out simultaneously | Rotate during a planned maintenance window and communicate in advance |
| Admin account compromised | Full user management access | Only one or two people should be admins; review quarterly |
| No audit trail for data uploads | Cannot trace who changed shared data | `user_id` is stored on every uploaded row; PostgreSQL logs are enabled on Azure |
| Bot returns data beyond what was asked | Sensitive information disclosure | Context passed to the model is assembled from the user's question and indexed data only — the model is not given a full database dump |

---

## Things to Keep on Top Of

| Task | How Often | Why |
|---|---|---|
| Rotate JWT_SECRET | Once a year or when someone leaves | All existing sessions are immediately invalidated |
| Rotate Azure OpenAI API key (nda-python-backend) | Per company security policy | Update in Function App settings after rotation |
| Review admin users in React app | Every quarter | Deactivate accounts for people who have left |
| Review Power Apps sharing groups | Every quarter | Remove leavers from approved Entra ID groups |
| Check AI Search index document count after each ingest | After every MPPR upload | Make sure all projects were indexed, not silently skipped |
| Check PostgreSQL nda_projects row count after each ingest | After every MPPR upload via Custom RAG | Same check for the pgvector side |
| Review SharePoint library permissions | Every six months | Make sure no broad sharing links have been created |