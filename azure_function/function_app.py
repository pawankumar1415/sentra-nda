import logging
import io
import math
import json
import os

import azure.functions as func
import pandas as pd
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — values are populated from environment / local.settings.json
# ─────────────────────────────────────────────────────────────────────────────
SEARCH_ENDPOINT   = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_API_KEY    = os.environ["AZURE_SEARCH_API_KEY"]
SEARCH_INDEX_NAME = os.environ["AZURE_SEARCH_INDEX_NAME"]

# Target Excel sheet and reporting period read from environment so this function
# can also handle P01, P02 … etc. without a code change.
SHEET_NAME        = os.environ.get("EXCEL_SHEET_NAME", "5a)NDA MPPR")

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# ─────────────────────────────────────────────────────────────────────────────
# Auto-create the Azure AI Search index on cold start if it doesn't exist
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_index_exists() -> None:
    """Creates the Azure AI Search index if it does not already exist.
    Called once at module load time (cold start) so it is transparent to callers.
    """
    index_client = SearchIndexClient(
        endpoint=SEARCH_ENDPOINT,
        credential=AzureKeyCredential(SEARCH_API_KEY),
    )

    # Check if the index already exists
    existing_names = [idx.name for idx in index_client.list_index_names()]
    if SEARCH_INDEX_NAME in existing_names:
        logging.info("Azure AI Search index '%s' already exists — skipping creation.", SEARCH_INDEX_NAME)
        return

    logging.info("Index '%s' not found — creating...", SEARCH_INDEX_NAME)

    fields = [
        SimpleField(name="id",                    type=SearchFieldDataType.String,  key=True,       filterable=True),
        SearchableField(name="ProjectName",        type=SearchFieldDataType.String,  filterable=True, sortable=True),
        SimpleField(name="ReportingPeriod",        type=SearchFieldDataType.String,  filterable=True, sortable=True,  facetable=True),
        SimpleField(name="DCA_RAG_Status",         type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="DCA_RAG_Movement",       type=SearchFieldDataType.Int32,   filterable=True, sortable=True),
        SimpleField(name="Baseline_RAG_Status",    type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="Capability_Capacity_RAG",type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="BusinessCase_Cost_P50",  type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="BusinessCase_Cost_P80",  type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="EAC_Cost",               type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="EAC_vs_Last_Period",      type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Baseline_Cost_P50",      type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Timeline_Delay_Days",    type=SearchFieldDataType.Int32,   filterable=True, sortable=True),
        SimpleField(name="CPI",                    type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="SPI",                    type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Pct_Complete",           type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SearchableField(name="NarrativeText",      type=SearchFieldDataType.String,  analyzer_name="en.microsoft"),
    ]

    index = SearchIndex(name=SEARCH_INDEX_NAME, fields=fields)
    index_client.create_or_update_index(index)
    logging.info("Index '%s' created successfully.", SEARCH_INDEX_NAME)


# Run once at module import (i.e. on every cold start)
try:
    _ensure_index_exists()
except Exception as _exc:
    # Do not crash the whole module — log and continue
    logging.warning("Could not auto-create index: %s", _exc)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Trigger entry point
# POST /api/process_mppr
# Body: multipart/form-data with a field named "file" containing the .xlsx file
#       and an optional field "reporting_period" (defaults to "Unknown")
# ─────────────────────────────────────────────────────────────────────────────
@app.route(route="process_mppr", methods=["POST"])
def process_mppr(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("process_mppr function triggered.")

    # ------------------------------------------------------------------
    # 1. Read the uploaded Excel file from the multipart body
    # ------------------------------------------------------------------
    try:
        file_data = req.files.get("file")
        if not file_data:
            return func.HttpResponse(
                json.dumps({"error": "No file provided. Send an .xlsx file in the 'file' field."}),
                status_code=400,
                mimetype="application/json",
            )

        reporting_period = req.form.get("reporting_period", "Unknown")
        file_stream = io.BytesIO(file_data.read())
        logging.info("File received. Reporting period: %s", reporting_period)

    except Exception as exc:
        logging.exception("Failed to read uploaded file: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to read file: {str(exc)}"}),
            status_code=400,
            mimetype="application/json",
        )

    # ------------------------------------------------------------------
    # 2. Parse the Excel file
    # ------------------------------------------------------------------
    try:
        projects = extract_projects(file_stream, reporting_period)
        logging.info("Extracted %d projects from %s.", len(projects), reporting_period)

        if not projects:
            return func.HttpResponse(
                json.dumps({"warning": "No valid projects found in the uploaded file."}),
                status_code=200,
                mimetype="application/json",
            )

    except Exception as exc:
        logging.exception("Failed to parse Excel file: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to parse Excel: {str(exc)}"}),
            status_code=500,
            mimetype="application/json",
        )

    # ------------------------------------------------------------------
    # 3. Push documents to Azure AI Search
    # ------------------------------------------------------------------
    try:
        uploaded, failed = push_to_ai_search(projects)
        logging.info("Upload complete. Succeeded: %d, Failed: %d", uploaded, failed)

    except Exception as exc:
        logging.exception("Failed to push to Azure AI Search: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to index documents: {str(exc)}"}),
            status_code=500,
            mimetype="application/json",
        )

    # ------------------------------------------------------------------
    # 4. Return summary response
    # ------------------------------------------------------------------
    return func.HttpResponse(
        json.dumps({
            "message": "Processing complete.",
            "reporting_period": reporting_period,
            "projects_extracted": len(projects),
            "documents_indexed": uploaded,
            "documents_failed": failed,
        }),
        status_code=200,
        mimetype="application/json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Parse the Excel file into a list of clean project dictionaries
# ─────────────────────────────────────────────────────────────────────────────
def extract_projects(file_stream: io.BytesIO, reporting_period: str) -> list[dict]:
    """
    Reads the '5a)NDA MPPR' tab (columns A to AK) of the provided Excel stream
    and returns a list of structured project dictionaries.

    The sheet has a complex multi-row header (rows 1-17) followed by data rows
    that alternate between:
        - A METADATA ROW  (project name in col B, RAG statuses in cols D, O, AK, etc.)
        - A NARRATIVE ROW (project name repeated in col A, full narrative text in col B)

    Column index reference (0-based after reading A:AK):
        A=0  B=1  C=2  D=3  E=4 … O=14 … W=22 … Z=25 … AK=36
    """

    def _safe(val):
        """Return None if value is NaN / NaT, else return as-is."""
        if val is None:
            return None
        if isinstance(val, float) and math.isnan(val):
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        return val

    def _str(val) -> str | None:
        v = _safe(val)
        return str(v).strip() if v is not None else None

    def _float(val) -> float | None:
        v = _safe(val)
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    def _int(val) -> int | None:
        v = _float(val)
        return int(round(v)) if v is not None else None

    # Read the entire A:AK range without inferring headers
    df = pd.read_excel(
        file_stream,
        sheet_name=SHEET_NAME,
        usecols="A:AK",
        header=None,
        dtype=str,              # Keep everything as strings initially
    )

    # Re-read with numeric dtypes for the numeric columns we need
    df_typed = pd.read_excel(
        file_stream,
        sheet_name=SHEET_NAME,
        usecols="A:AK",
        header=None,
    )

    projects = []

    for idx in range(len(df)):
        if idx < 17:          # Skip header rows
            continue

        col_A = df_typed.iat[idx, 0]   # Project name (narrative row) OR NaN
        col_B = df_typed.iat[idx, 1]   # Project name (metadata row) OR narrative text

        # ── Detect the "Table of Changes" footer and stop ──────────────────
        b_str = _str(col_B)
        if b_str and "Table of Changes" in b_str:
            break

        # ── Metadata row: col A is NaN, col B is a short project name ──────
        if _safe(col_A) is None and b_str and len(b_str) < 120:

            # Look ahead one row for the narrative
            narrative = None
            if idx + 1 < len(df_typed):
                next_col_A = _str(df_typed.iat[idx + 1, 0])
                next_col_B = _str(df_typed.iat[idx + 1, 1])
                # Narrative row: col A repeats the project name, col B is the long text
                if next_col_A and next_col_A == b_str and next_col_B and len(next_col_B) > 120:
                    narrative = next_col_B.strip()

            if not narrative:
                continue      # Skip projects without a narrative

            # ── Map column positions to schema fields ──────────────────────
            # Column D  (index 3)  = DCA RAG
            # Column O  (index 14) = Baseline RAG
            # Column AK (index 36) = Capability & Capacity RAG
            # Column W  (index 22) = EAC Cost (Current Baseline P50 lifecycle)
            # Column X  (index 23) = EAC Cost (Estimate At Complete)
            # Column Z  (index 25) = Forecast vs Last Period (Days)
            # Column E  (index 4)  = DCA RAG movement vs last period (+1/0/-1)
            doc = {
                "id": f"{_make_id(b_str)}_{reporting_period}",
                "ProjectName": b_str,
                "ReportingPeriod": reporting_period,
                "DCA_RAG_Status": _str(df_typed.iat[idx, 3]),
                "DCA_RAG_Movement": _int(df_typed.iat[idx, 4]),
                "Baseline_RAG_Status": _str(df_typed.iat[idx, 14]),
                "Capability_Capacity_RAG": _str(df_typed.iat[idx, 36]),
                "BusinessCase_Cost_P50": _float(df_typed.iat[idx, 7]),
                "BusinessCase_Cost_P80": _float(df_typed.iat[idx, 8]),
                "EAC_Cost": _float(df_typed.iat[idx, 23]),
                "EAC_vs_Last_Period": _float(df_typed.iat[idx, 24]),
                "Baseline_Cost_P50": _float(df_typed.iat[idx, 22]),
                "Timeline_Delay_Days": _int(df_typed.iat[idx, 26]),
                "CPI": _float(df_typed.iat[idx, 31]),
                "SPI": _float(df_typed.iat[idx, 32]),
                "Pct_Complete": _float(df_typed.iat[idx, 33]),
                "NarrativeText": narrative,
            }

            projects.append(doc)

    return projects


def _make_id(name: str) -> str:
    """Create an Azure Search-safe document ID from a project name."""
    safe = (
        name
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("/", "_")
        .replace("&", "and")
        .replace(".", "")
        .replace(",", "")
    )
    return safe[:128]   # Azure AI Search key max length is 1024, truncate for safety


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Push documents to Azure AI Search
# ─────────────────────────────────────────────────────────────────────────────
def push_to_ai_search(documents: list[dict]) -> tuple[int, int]:
    """
    Uploads the list of project documents to the configured Azure AI Search index.

    Returns a (succeeded_count, failed_count) tuple.

    NOTE: This function uses the `mergeOrUpload` action so that re-running the
    function for the same reporting period will update existing records rather
    than creating duplicates.
    """
    client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_API_KEY),
    )

    # Azure AI Search accepts up to 1000 documents per batch
    BATCH_SIZE = 500
    succeeded = 0
    failed = 0

    for i in range(0, len(documents), BATCH_SIZE):
        batch_docs = documents[i : i + BATCH_SIZE]
        results = client.upload_documents(documents=batch_docs)

        for result in results:
            if result.succeeded:
                succeeded += 1
            else:
                failed += 1
                logging.warning(
                    "Failed to index document '%s': %s",
                    result.key,
                    result.error_message if hasattr(result, "error_message") else "unknown error",
                )

    return succeeded, failed
