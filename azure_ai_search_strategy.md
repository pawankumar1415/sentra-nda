# Azure AI Search Indexing Strategy for NDA Portfolio Data

Based on the structure of the `P07 Exec Project Summary FINAL.xlsx` file (specifically the `5a)NDA MPPR` tab, columns A to AK) and the `NDA Questionnaire.docx` expected queries, here is the proposed strategy for ingesting and indexing this data into Azure AI Search to be used by Foundry Agents.

## 1. Data Cleaning & Transformation
The raw extraction revealed a complex, multi-row header structure and sparse rows. 
*   **Header Normalization**: We need a Python script to map the complex headers (spanning rows 1-5) into flat, strongly-typed JSON keys. 
    *   *Example:* Merge "Business Case (P50)" and "Project Cost" into `business_case_cost_p50`.
*   **Row Consolidation**: Fill down `Project Name` and merge associated narrative rows into a single project record.
*   **Filtering**: Drop rows where `Project Name` is `NaN` or unassigned.

## 2. Document Definition (Azure AI Search Index Schema)
Each document in the index will represent a **Single Project for a Specific Reporting Period**. 

**Primary Key:** `projectId_periodId` (e.g., `projXYZ_P07`)

### Field Structure:
*   **Id** (`Edm.String`, Key)
*   **ProjectName** (`Edm.String`, Searchable, Filterable, Sortable)
*   **ReportingPeriod** (`Edm.String`, Filterable, Sortable) - *Crucial for Month-over-Month comparisons (Category 6 & 7).*
*   **SRO** (`Edm.String`, Filterable)
*   **DCA_RAG_Status** (`Edm.String`, Filterable, Facetable) - *For "How many projects are Red?" (Category 1).*
*   **Baseline_RAG_Status** (`Edm.String`, Filterable, Facetable)
*   **Capability_Capacity_RAG** (`Edm.String`, Filterable)
*   **EAC_Cost** (`Edm.Double`, Sortable, Filterable) - *For financial ranking queries (Category 4 & 9).*
*   **CostVariance_Percentage** (`Edm.Double`, Sortable, Filterable)
*   **Timeline_Delay_Days** (`Edm.Int32`, Sortable, Filterable) - *For schedule slippage queries (Category 5).*
*   **NarrativeText** (`Edm.String`, Searchable) - The consolidated text from the main narrative block.
*   **NarrativeTextVector** (`Collection(Edm.Single)`, VectorField) - Generative AI embeddings for semantic search.

## 3. Retrieval Strategy for Foundry Agents
To answer the complex questions in the `NDA Questionnaire.docx`, the Agent needs a **HyDE (Hybrid Search + Data Extraction)** approach:

1.  **Structured Filtering (OData)**: 
    *   *Question:* "How many projects are Red?"
    *   *Agent Action:* Emits API call to Azure Search `filter=DCA_RAG_Status eq 'Red'`. Counts the results.
2.  **Semantic / Vector Search**: 
    *   *Question:* "What are the main concerns in Project Gamma?"
    *   *Agent Action:* Performs a Hybrid Vector Search against `NarrativeTextVector` filtering by `ProjectName eq 'Project Gamma'`. The LLM synthesizes the `NarrativeText` to answer the question.
3.  **Cross-Period Aggregation**:
    *   *Question:* "How has Project Alpha performed over last 3 periods?"
    *   *Agent Action:* Queries documents where `ProjectName eq 'Project Alpha'`, sorting by `ReportingPeriod desc`, taking top 3. LLM analyzes the trend.

## 4. Next Implementation Steps
1.  **Write the Data Cleansing Script**: A pandas script to fully clean the `excel_mppr_output` CSV into a structured list of JSON objects matching the schema above.
2.  **Azure AI Search Setup**: Define the index structure programmatically (using `azure-search-documents` SDK).
3.  **Generate Embeddings & Push**: Run the JSON objects through an embedding model (e.g., `text-embedding-ada-002`) and upload them to the index.
