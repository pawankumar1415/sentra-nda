# NDA Narrative Validation — Architecture Flows

Two approaches to the same problem. Both share the same Azure OpenAI GPT and embedding deployments.

---

## Approach 1 — Azure AI Foundry Agent

**Selected path:**
`SharePoint` → `Power Automate` → `Azure Function` → `Azure AI Search` → `Foundry Agent` → `GPT` → `Copilot / Teams`

```mermaid
flowchart TD
    A["📁 SharePoint\nExcel uploaded to\ndocument library"] -->|"Power Automate\ntrigger on new file"| B["⚡ Azure Function\n/api/ingest\nazure_function/"]
    B --> C["🔍 Azure AI Search\nnda-mppr-projects index"]

    D["👤 User message in Teams\n'Validate this narrative...'"] --> E["🤖 Copilot Agent\nMicrosoft Teams Tab"]
    E -->|"calls"| F["🤖 Azure AI Foundry Agent\nnda-narrative-validator-v1"]
    C -->|"AI Search Tool"| F
    G["🛠️ Function Tools\ncheck_eac_variance"] --> F
    F --> H["🧠 Azure OpenAI GPT\ngpt-5.1-chat"]
    H --> I["📋 Response\nback in Teams"]

    style A fill:#0078d4,color:#fff
    style E fill:#5c2d91,color:#fff
    style F fill:#6a2d9f,color:#fff
    style H fill:#2d9f6a,color:#fff
```

---

## Approach 2 — Open-Source RAG Pipeline

**Selected path:**
`SharePoint` → `Power Automate` → `Azure Function /ingest` → `PGVector` → `Azure Function /validate` → `GPT` → `Copilot / Teams`

```mermaid
flowchart TD
    A["📁 SharePoint\nExcel uploaded to\ndocument library"] -->|"Power Automate\ntrigger on new file"| B["⚡ Azure Function\nPOST /api/ingest\nrag_function/"]
    B --> C["🔢 Azure OpenAI\ntext-embedding-3-large\n3072 dims"]
    C --> D["🗄️ Azure PostgreSQL\n+ pgvector\nnda_projects"]

    E["👤 User message in Teams\n'Validate this narrative...'"] --> F["🤖 Copilot Agent\nMicrosoft Teams Tab"]
    F -->|"calls"| G["⚡ Azure Function\nPOST /api/validate\nrag_function/"]
    G --> H["🔢 Azure OpenAI\nEmbed query"]
    H -->|"cosine similarity"| D
    D -->|"top-5 chunks"| I["📊 EAC Variance\nlifecycle_eac_variance.xlsx"]
    I --> J["🧠 Azure OpenAI GPT\ngpt-5.1-chat"]
    J --> K["📋 Response\nback in Teams"]

    style A fill:#0078d4,color:#fff
    style F fill:#5c2d91,color:#fff
    style D fill:#6a2d9f,color:#fff
    style J fill:#2d9f6a,color:#fff
```

---

## Approach 3 — Custom RAG + React Frontend (Current Production System)

**Selected path:**
`SharePoint List / Local Upload` → `React Frontend` → `Azure Function /api/*` → `PostgreSQL + pgvector` → `GPT` → `React Frontend`

```mermaid
flowchart LR
    SP["SharePoint\nMPPR Files"]
    LU["Local Upload\nMPPR Excel"]

    subgraph FA["Azure Function App (Python)"]
        INGEST["File Processor"]
        VAL["Validator"]
        CHAT["Chat Assistant"]
    end

    subgraph STORE["Storage"]
        PG[("Project Database\n+ Vector Search")]
        BLOB["Guidance Document"]
    end

    GPT["Azure OpenAI\nGPT"]
    UI["React Web App\nAzure Static Web Apps"]

    SP -->|"reads files"| INGEST
    LU -->|"upload"| INGEST
    INGEST -->|"stores projects"| PG
    UI -->|"submit narrative"| VAL
    BLOB -->|"good practice rules"| VAL
    PG -->|"similar projects + EAC data"| VAL
    VAL -->|"prompt"| GPT
    GPT -->|"validation result"| UI
    UI -->|"ask question"| CHAT
    PG -->|"portfolio data"| CHAT
    CHAT -->|"prompt"| GPT
    GPT -->|"answer"| UI

    style SP fill:#0078d4,color:#fff
    style LU fill:#0078d4,color:#fff
    style UI fill:#20b2aa,color:#fff
    style PG fill:#6a2d9f,color:#fff
    style GPT fill:#2d9f6a,color:#fff
    style BLOB fill:#e8a020,color:#fff
```

---

## Side-by-Side Comparison

| | Approach 1 — Foundry Agent | Approach 2 — Open-Source RAG |
|---|---|---|
| **Document source** | SharePoint → Power Automate → Azure Function | SharePoint → Power Automate → Azure Function |
| **Vector store** | Azure AI Search (managed) | Azure PostgreSQL + pgvector |
| **Embedding** | Azure AI Search built-in | text-embedding-3-large (3072d) |
| **Orchestration** | Azure AI Foundry Agent | Direct Python RAG pipeline |
| **User interface** | Copilot Agent / Teams Tab | Copilot Agent / Teams Tab |
| **EAC Tools** | Foundry Function Tools | Python functions in validate.py |
| **Auth** | Service principal + AI Search role | Entra ID token for PostgreSQL |
| **Status** | ⏳ Awaiting Search Index Data Reader | ✅ Pipeline built, DB live |
