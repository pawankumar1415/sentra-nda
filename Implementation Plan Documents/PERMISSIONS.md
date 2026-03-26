# Azure Permissions & RBAC — NDA Narrative Validation System

**Resource Group:** `sellafield-dpmo-dev`
**Subscription:** `530e4a26-4cf6-44b1-9549-84ae1b36b4d9`

This document records all Azure RBAC role assignments and configuration steps completed during the initial setup and post-migration to the `sellafield-dpmo-dev` resource group.

---

## Resources Overview

| Resource Name | Type | Notes |
|---|---|---|
| `movar-secure-azure-resource` | Azure AI Services / CognitiveServices | Hosts Azure OpenAI deployments; **recreated** during migration (keys changed) |
| `movar-nda-aisearch` | Azure AI Search | Holds index `nda-mppr-projects` used by Foundry knowledge base |
| `nda-narrative` | Azure AI Foundry Project | Agent + knowledge base; **recreated** during migration |
| `nda-python-backend-...` | Azure Function App (Custom) | Custom RAG pipeline (`rag_function/`) |
| `nda-foundry-api-...` | Azure Function App (Foundry) | Azure AI Foundry agent API (`agent/`) |
| Storage Account | Azure Storage | Blob containers for Foundry file storage |

---

## RBAC Role Assignments

Both Function Apps run under **system-assigned Managed Identities**. The identities were granted the following roles:

### On `movar-secure-azure-resource` (Azure OpenAI / AI Services)

| Principal | Role | Purpose |
|---|---|---|
| Custom Function App MI | `Cognitive Services OpenAI User` | Allows the function to call Azure OpenAI (embeddings + chat completions) |
| Foundry Function App MI | `Cognitive Services OpenAI User` | Same — Foundry agent needs OpenAI access |

**Where to set:** Azure Portal → `movar-secure-azure-resource` → Access control (IAM) → Role assignments → Add role assignment.

---

### On Storage Account

| Principal | Role | Purpose |
|---|---|---|
| Custom Function App MI | `Storage Blob Data Contributor` | Read/write blob containers (function app storage, deployment artefacts) |
| Foundry Function App MI | `Storage Blob Data Contributor` | Same |

**Where to set:** Azure Portal → Storage Account → Access control (IAM) → Role assignments.

---

### On `movar-nda-aisearch` (Azure AI Search)

| Principal | Role | Purpose |
|---|---|---|
| Custom Function App MI | `Search Index Data Contributor` | Read/write the `nda-mppr-projects` index |
| Foundry Function App MI | `Search Index Data Contributor` | Same |
| Custom Function App MI | `Search Service Contributor` | Manage the search service (create/modify indexes) |
| Foundry Function App MI | `Search Service Contributor` | Same |

**Where to set:** Azure Portal → `movar-nda-aisearch` → Access control (IAM) → Role assignments.

---

## Azure AI Search — Index Configuration

**Index name:** `nda-mppr-projects`

The index was created by the ingest pipeline. For the Azure AI Foundry knowledge base to work, a **semantic configuration** must be present on the index.

### Semantic Configuration (added manually in portal)

| Setting | Value |
|---|---|
| Semantic config name | (auto-generated or custom) |
| Title field | `ProjectName` |
| Content field | `NarrativeText` |
| Keyword fields | `DCA_RAG_Status`, `ReportingPeriod` |

**Where to set:** Azure Portal → `movar-nda-aisearch` → Indexes → `nda-mppr-projects` → Semantic configurations → Add.

Also ensure **Semantic Search** is enabled on the search service tier:

Azure Portal → `movar-nda-aisearch` → Settings → Semantic Search → Enable (Free or Standard tier).

---

## Azure AI Foundry — Setup

### Connections

The Foundry project (`nda-narrative`) requires two connections:

| Connection Name | Type | Target |
|---|---|---|
| `movar-nda-aisearch` | Azure AI Search | `movar-nda-aisearch` service |
| (default AI Services connection) | Azure OpenAI | `movar-secure-azure-resource` |

**Where to set:** Azure AI Foundry Portal → `nda-narrative` project → Management → Connections.

### Knowledge Base

| Setting | Value |
|---|---|
| Knowledge base name | `knowledgebase114` |
| Data source | `ks-searchindex-552` (AI Search connection `movar-nda-aisearch`) |
| Index | `nda-mppr-projects` |
| Status | Active |

The knowledge base status showed as **Active** after the semantic configuration was added to the index.

### Memory Store

Memory (thread persistence) is configured via the Azure AI Foundry built-in **Memory** feature in the agent settings. This provides thread-level conversation memory for the Foundry agent.

---

## Function App — Environment Variables Required

### Custom Approach (`nda-python-backend-...`)

| Variable | Source |
|---|---|
| `POSTGRES_HOST` | Azure PostgreSQL server hostname |
| `POSTGRES_DB` | Database name |
| `POSTGRES_USER` | Database username |
| `POSTGRES_PASSWORD` | Database password (or leave blank for Managed Identity) |
| `AZURE_OPENAI_ENDPOINT` | `movar-secure-azure-resource` → Keys and Endpoint → OpenAI tab |
| `AZURE_OPENAI_API_KEY` | `movar-secure-azure-resource` → Keys and Endpoint → OpenAI tab → Key 1 |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment name for embeddings (e.g. `text-embedding-3-large`) |
| `AZURE_OPENAI_EMBEDDING_DIMS` | `3072` (matches text-embedding-3-large) |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Deployment name for GPT (e.g. `gpt-5.1-chat`) |
| `JWT_SECRET` | Random secret for signing JWT tokens — generate once and never change |

### Foundry Approach (`nda-foundry-api-...`)

| Variable | Source |
|---|---|
| `AZURE_FOUNDRY_PROJECT_ENDPOINT` | `https://movar-secure-azure-resource.services.ai.azure.com/api/projects/nda-narrative` |
| `AZURE_AI_SEARCH_ENDPOINT` | `movar-nda-aisearch` → Overview → Url |
| `AZURE_AI_SEARCH_INDEX` | `nda-mppr-projects` |
| `AZURE_OPENAI_ENDPOINT` | Same as custom approach |
| `AZURE_OPENAI_API_KEY` | Same as custom approach |

---

## Key Migration Notes

These issues were discovered and resolved during the migration from `rg-Yash.Desai-2452` → `sellafield-dpmo-dev`:

| Issue | Root Cause | Fix |
|---|---|---|
| Azure OpenAI 401 on custom approach | `movar-secure-azure-resource` was **recreated** (not moved) — new API keys issued | Update `AZURE_OPENAI_API_KEY` in Function App settings with new Key 1 |
| Foundry agent 500 "project does not exist" | `AZURE_FOUNDRY_PROJECT_ENDPOINT` still pointed to old deleted project | Update to new project endpoint: `https://movar-secure-azure-resource.services.ai.azure.com/api/projects/nda-narrative` |
| Foundry knowledge base "missing semantic configuration" | Index `nda-mppr-projects` was created by code without a semantic config block | Add semantic configuration manually in portal (title, content, keywords fields) |
| AI Search tier warning | Semantic search requires Free or Standard tier to be explicitly enabled | Enable Semantic Search in AI Search service settings |

---

## How to Find API Keys (for future reference)

### Azure OpenAI API Key
1. Azure Portal → search for `movar-secure-azure-resource`
2. Left menu → **Keys and Endpoint**
3. Click the **OpenAI** tab (not the default "AI Services" tab)
4. Copy **KEY 1**

### Function App Host Key
1. Azure Portal → Function App → **Functions** → Select any function → **Function Keys**
2. Copy the `default` key
3. Pass as `?code=<key>` in URL when calling the API

### PostgreSQL Connection String
1. Azure Portal → PostgreSQL Flexible Server → **Connect** or **Connection strings**
2. Use host, port, database name, username

---

## Periodic Maintenance

| Task | Frequency | Notes |
|---|---|---|
| Rotate `JWT_SECRET` | Annually or on staff change | All existing tokens will be invalidated — users must log in again |
| Rotate Azure OpenAI API Key | Per security policy | Update `AZURE_OPENAI_API_KEY` in Function App settings after rotation |
| Review admin users | Quarterly | Use `/admin/users` panel to review and deactivate stale accounts |
| Check pgvector index health | If validation slows | `REINDEX INDEX nda_projects_embedding_idx;` via psql |