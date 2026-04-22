# Foundry Approach: End-to-End Deployment Guide

There are **two separate deployments** needed for the Foundry approach:

```
[SharePoint] → [Power Automate] → [Function App 1: nda-ingest]  → [Azure AI Search]
                                                                         ↑
              [Frontend / Teams] → [Function App 2: nda-agent]  → [AI Foundry Agent]
                                                                   (reads AI Search)
```

---

## Part 1: Deploy the Excel Ingestion Function (`azure_function/`)

This Function processes uploaded Excel files and pushes structured data to Azure AI Search.

### 1a. Create a NEW Azure Function App for Ingest

> [!IMPORTANT]
> This is **separate** from your RAG function app (`nda-python-backend`). Create a brand new one.

1. Go to **Azure Portal → Function Apps → + Create**
2. Settings:
   - **Name**: `nda-ingest` (or any name you choose)
   - **Runtime**: Python 3.11
   - **Region**: UK South (same as your other resources)
   - **Plan**: Consumption (Serverless)
3. Create and wait for deployment.

### 1b. Enable Managed Identity on nda-ingest

1. Go to **`nda-ingest` → Identity → System assigned → On → Save**
2. Note the **Object ID** of the new Managed Identity.

### 1c. Assign Roles to nda-ingest Managed Identity

Run these Azure CLI commands (replace `<MI_OBJECT_ID>` with the Object ID from above):

```bash
# Allow writing to AI Search index
az role assignment create \
  --role "Search Index Data Contributor" \
  --assignee-object-id <MI_OBJECT_ID> \
  --assignee-principal-type ServicePrincipal \
  --scope "/subscriptions/530e4a26-4cf6-44b1-9549-84ae1b36b4d9/resourceGroups/rg-Yash.Desai-2452/providers/Microsoft.Search/searchServices/movar-nda-aisearch"
```

### 1d. Configure App Settings for nda-ingest

In **Azure Portal → nda-ingest → Configuration → Application settings**, add:

| Key | Value |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | `https://movar-nda-aisearch.search.windows.net` |
| `AZURE_SEARCH_INDEX_NAME` | `nda-mppr-projects` |

*(No API key needed — the Managed Identity handles auth automatically).*

### 1e. Deploy the Code

```powershell
cd azure_function
func azure functionapp publish nda-ingest
```

---

## Part 2: Deploy the Agent API Function ([agent/](../agent/agent_runner.py#95-144))

The Agent needs to be exposed as an HTTP API so the frontend and Power Automate can call it.

### 2a. Create a NEW Azure Function App for the Agent

1. Go to **Azure Portal → Function Apps → + Create**
2. Settings:
   - **Name**: `nda-agent-api`
   - **Runtime**: Python 3.11
   - **Region**: UK South
3. Create and wait.

### 2b. Enable Managed Identity on nda-agent-api

1. Go to **`nda-agent-api` → Identity → System assigned → On → Save**
2. Note the **Object ID**.

### 2c. Assign Roles to nda-agent-api Managed Identity

```bash
# Test 3: Access to Foundry Agents API
az role assignment create \
  --role "Azure AI Developer" \
  --assignee-object-id <MI_OBJECT_ID> \
  --assignee-principal-type ServicePrincipal \
  --scope "/subscriptions/530e4a26-4cf6-44b1-9549-84ae1b36b4d9/resourceGroups/rg-Yash.Desai-2452/providers/Microsoft.CognitiveServices/accounts/movar-secure-azure-resource"

# Test 5: Read from AI Search index
az role assignment create \
  --role "Search Index Data Reader" \
  --assignee-object-id <MI_OBJECT_ID> \
  --assignee-principal-type ServicePrincipal \
  --scope "/subscriptions/530e4a26-4cf6-44b1-9549-84ae1b36b4d9/resourceGroups/rg-Yash.Desai-2452/providers/Microsoft.Search/searchServices/movar-nda-aisearch"

# OpenAI model access
az role assignment create \
  --role "Cognitive Services OpenAI User" \
  --assignee-object-id <MI_OBJECT_ID> \
  --assignee-principal-type ServicePrincipal \
  --scope "/subscriptions/530e4a26-4cf6-44b1-9549-84ae1b36b4d9/resourceGroups/rg-Yash.Desai-2452/providers/Microsoft.CognitiveServices/accounts/movar-secure-azure-resource"
```

### 2d. Add Managed Identity to Foundry Project

1. Go to **https://ai.azure.com** → Project `movar-secure-azure`
2. **Settings → Users → + New user**
3. Search for the Managed Identity by its Object ID
4. Assign role: **Azure AI Developer**

### 2e. Configure App Settings for nda-agent-api

| Key | Value |
|---|---|
| `AZURE_FOUNDRY_PROJECT_ENDPOINT` | `https://movar-secure-azure-resource.services.ai.azure.com/api/projects/movar-secure-azure` |
| `AZURE_FOUNDRY_MODEL_DEPLOYMENT` | `gpt-5.1-chat` |
| `AZURE_AI_SEARCH_CONNECTION_NAME` | *(full resource ID from Foundry → Connected Resources)* |
| `AZURE_SEARCH_INDEX_NAME` | `nda-mppr-projects` |
| `AZURE_SEARCH_ENDPOINT` | `https://movar-nda-aisearch.search.windows.net` |
| `USE_AI_SEARCH` | `true` |

### 2f. Add an HTTP Endpoint to agent_runner.py

The [agent/](../agent/agent_runner.py#95-144) folder currently only has a CLI runner. We need to add [function_app.py](../rag_function/function_app.py) to expose it as an HTTP API:

```python
# agent/function_app.py  (NEW FILE TO CREATE)
import azure.functions as func
import json
from agent.agent_runner import validate_narrative

app = func.FunctionApp()

@app.route(route="validate", methods=["POST"])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()
    result = validate_narrative(
        project_name=body.get("project_name", ""),
        narrative_text=body.get("narrative", ""),
        period=body.get("period", "")
    )
    return func.HttpResponse(json.dumps(result), mimetype="application/json")
```

### 2g. Deploy the Agent Code

```powershell
cd agent
func azure functionapp publish nda-agent-api
```

---

## Part 3: Power Automate Flow (SharePoint Trigger)

Once both Function Apps are deployed:

1. Create a new **Power Automate flow**
2. Trigger: **When a file is created in SharePoint** (your Excel upload folder)
3. Action → **HTTP POST** to `https://nda-ingest.azurewebsites.net/api/process_mppr`
4. Body: Pass the file attachment bytes

---

## Verification

After deployment, test with:
```bash
curl -X POST "https://nda-agent-api.azurewebsites.net/api/validate" \
  -H "Content-Type: application/json" \
  -d '{"narrative": "The SRO DCA remains Amber...", "project_name": "Sellafield", "period": "P07"}'
```
