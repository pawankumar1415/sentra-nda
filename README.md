# NDA Narrative Validation System

An AI-powered tool that validates NDA project reporting narratives against writing guidelines and live data movements, surfaced via Microsoft Teams.

---

## How It All Works — The Full Picture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  EVERY REPORTING PERIOD (one-off per period)                                │
│                                                                              │
│  1. Someone uploads P07 Excel file                                           │
│                    │                                                         │
│                    ▼                                                         │
│         [azure_function/]                                                    │
│         Azure Function (HTTP POST)                                           │
│         • Parses tab "5a)NDA MPPR", columns A→AK                            │
│         • Extracts all 14 project records                                    │
│         • Pushes to Azure AI Search index: nda-mppr-projects                │
│                    │                                                         │
│                    ▼                                                         │
│         Azure AI Search Index          lifecycle_eac_variance.xlsx          │
│         (narratives, RAG, costs)  ◄──  (uploaded separately)                │
└──────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  DURING THE REPORTING WINDOW (daily use by reporters / PMO)                  │
│                                                                              │
│  User in Teams asks: "Validate the narrative for Box Encapsulation Plant"   │
│                    │                                                         │
│                    ▼                                                         │
│         [Phase 3 - bot/] ← coming next                                      │
│         Azure Bot Service (Teams Channel)                                    │
│                    │                                                         │
│                    ▼                                                         │
│         [agent/]                                                             │
│         Azure AI Foundry Agent (Python SDK)                                  │
│         • Searches AI Search for project narrative & RAG data                │
│         • Calls check_eac_variance() → reads lifecycle_eac_variance.xlsx    │
│         • Applies Good Practice guidelines from its system prompt            │
│         • Returns: Layer 1 (guidance) + Layer 2 (data movement) results     │
│                    │                                                         │
│                    ▼                                                         │
│         Bot sends formatted reply back to Teams user                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure Explained

```
Custom Solution/
│
├── README.md                        ← You are here
├── requirements.txt                 ← Root Python dependencies (dev/venv)
├── create_index.py                  ← One-off: creates the Azure AI Search index
├── clean_mppr_data.py               ← Utility: test Excel extraction locally
├── test_local.py                    ← End-to-end test for the Azure Function
│
├── NDA Data/
│   ├── P07 Exec Project Summary FINAL.xlsx   ← Source reporting workbook
│   ├── lifecycle_eac_variance.xlsx            ← EAC/schedule variance lookup
│   └── Good Practice Reference for checking.docx  ← Guidelines (now embedded in agent)
│
├── azure_function/                  ← COMPONENT 1: Data Ingestion Pipeline
│   ├── function_app.py              ← Azure Function (HTTP trigger)
│   ├── host.json                    ← Runtime config
│   ├── local.settings.json          ← Credentials for local dev ← FILL THIS IN
│   ├── requirements.txt             ← Packages for the Function runtime
│   └── README.md                    ← Function-specific docs
│
└── agent/                           ← COMPONENT 2: AI Agent Core
    ├── agent_runner.py              ← Main logic: validate / batch / generate
    ├── tools.py                     ← EAC variance Python Function Tools
    ├── system_prompt.py             ← Good Practice guidelines as agent instructions
    ├── config.py                    ← Agent credentials + threshold constants
    ├── local.settings.json          ← Credentials for local dev ← FILL THIS IN
    └── requirements.txt             ← Agent-specific packages
```

---

## Step 1 — Prerequisites (do once)

### Azure Resources Required

| Resource | Purpose | Already exists? |
|---|---|---|
| Azure AI Search (`nda-ai-search`) | Stores project data as searchable documents | ✅ Yes |
| Azure AI Foundry Project | Hosts the AI Agent and model deployment | ✅ Yes |
| Azure Function App | Runs the Excel ingestion pipeline | ⬜ Deploy |
| Azure Bot Service | Connects the agent to Teams | ⬜ Phase 3 |

### Connect AI Search to Foundry (one-time portal step)
1. Open **Azure AI Foundry Portal**
2. Go to **Management centre → Connected Resources**
3. Click **+ New connection → Azure AI Search**
4. Select `nda-ai-search` → Save
5. Note down the **connection display name** — you'll need it as `AZURE_AI_SEARCH_CONNECTION_NAME`

---

## Step 2 — Create the AI Search Index (once)

```powershell
cd "C:\Users\rahul\Repos\Sentra Project BSBI\Custom Solution"
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python create_index.py
```

This creates the `nda-mppr-projects` index with the full schema.  
Safe to re-run — it uses `create_or_update_index` so it won't destroy existing data.

---

## Step 3 — Upload a Reporting Period (e.g. P07)

### Option A — Using the REST API (cURL)
```bash
curl -X POST "https://<YOUR_FUNCTION_APP>.azurewebsites.net/api/process_mppr?code=<FUNCTION_KEY>" \
  -F "file=@P07 Exec Project Summary FINAL.xlsx" \
  -F "reporting_period=P07"
```

### Option B — Locally (for testing)
```powershell
# Terminal 1: start the function
cd azure_function
pip install -r requirements.txt
func start

# Terminal 2: run the test
cd ..
venv\Scripts\python test_local.py
```

**What happens:** The function reads the Excel file, extracts all projects from `5a)NDA MPPR` tab (columns A→AK), and pushes 14 structured JSON documents to Azure AI Search. If a period is re-uploaded, existing records are updated (not duplicated).

---

## Step 4 — Use the AI Agent

### Locally (interactive CLI)
```powershell
# Fill in agent/local.settings.json first (see Credentials section below)
cd "C:\Users\rahul\Repos\Sentra Project BSBI\Custom Solution"
venv\Scripts\pip install azure-ai-projects azure-ai-agents azure-identity
venv\Scripts\python -m agent.agent_runner
```

Then type commands:
- `validate` → validates a project narrative (NDA-01)
- `batch` → finds all projects needing commentary (NDA-002)
- `generate` → drafts a compliant narrative (NDA-003)

### As a Python module (used by the Bot/API layer)
```python
from agent.agent_runner import validate_narrative, batch_validate, generate_narrative

# Validate a single narrative
result = validate_narrative(
    project_name="Box Encapsulation Plant",
    narrative_text="The project...",
    period="P07"
)
print(result["validation_result"])

# Batch check all projects with material movements
batch = batch_validate("2025-P07")
print(batch["exceptions_report"])
```

---

## Step 5 — Deploy the Azure Function

```powershell
cd azure_function
func azure functionapp publish <YOUR_FUNCTION_APP_NAME>
```

After deployment, set the following **Application Settings** in the Azure Portal (Function App → Configuration):

| Setting | Value |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | `https://nda-ai-search.search.windows.net` |
| `AZURE_SEARCH_API_KEY` | Your Admin API Key |
| `AZURE_SEARCH_INDEX_NAME` | `nda-mppr-projects` |

---

## Step 6 — Deploy the Agent (Phase 3 — Bot for Teams)

> ⬜ Coming in Phase 3. The `bot/` package will wrap the agent and connect it to Teams via Azure Bot Service.

**Summary of what Phase 3 will build:**
1. `bot/app.py` — A FastAPI/Flask app that receives Teams messages via Bot Framework
2. `bot/bot_handler.py` — Translates Teams messages to `agent_runner` calls
3. Deploy to **Azure App Service** or **Azure Container Apps**
4. Register in **Azure Bot Service** → enable Teams channel → publish to org via Teams Admin Centre

---

## Credentials Reference

### azure_function/local.settings.json
```json
{
  "Values": {
    "AZURE_SEARCH_ENDPOINT": "https://nda-ai-search.search.windows.net",
    "AZURE_SEARCH_API_KEY": "<your admin key>",
    "AZURE_SEARCH_INDEX_NAME": "nda-mppr-projects"
  }
}
```

### agent/local.settings.json
```json
{
  "Values": {
    "AZURE_FOUNDRY_PROJECT_ENDPOINT": "https://<resource>.services.ai.azure.com/api/projects/<project>",
    "AZURE_FOUNDRY_MODEL_DEPLOYMENT": "gpt-4o",
    "AZURE_AI_SEARCH_CONNECTION_NAME": "<connection display name from Foundry portal>",
    "AZURE_SEARCH_INDEX_NAME": "nda-mppr-projects"
  }
}
```

> ⚠️ **Never commit `local.settings.json` files to source control.** They are gitignored by default.

---

## The Two-Layer Validation Explained

### Layer 1 — Guidance & Structure
The agent checks the narrative against the **Good Practice Reference** guidelines (embedded in its system prompt):
- Is it written as a flowing paragraph (not bullet points)?
- Are all acronyms expanded on first use?
- Are all dates written in full (e.g. "15th May 2024")?
- Do the sentence templates appear in order (DCA → benefit → cost → schedule → Baseline RAG → Capability RAG)?
- Are figures consistent with reported data?

### Layer 2 — Data-Driven Movement Check
The agent calls `check_eac_variance()` which reads `lifecycle_eac_variance.xlsx`:

| EAC Movement | Action |
|---|---|
| < £50k | No comment required |
| ≥ £0.1m (one decimal place) | Must appear in narrative |
| ≥ £0.5m | Requires explicit explanation |
| Any schedule slippage | Must be mentioned and explained |

---

## EAC Variance Thresholds (from Joanne's requirements)

> NDA reporting is to £0.1m (one decimal place). Very small movements (< £50k) may not change what is shown on the report. Larger movements (£0.5m+, £1m+, or significant schedule slippage) should trigger clear explanation.
