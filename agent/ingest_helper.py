"""
agent/ingest_helper.py

Handles Excel ingestion and Azure AI Search indexing for the MPPR reports.
Extracted from the standalone azure_function/ app and updated to use
DefaultAzureCredential (Managed Identity) instead of API keys.
"""

import io
import logging
import math
import os

import pandas as pd
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
)
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT        = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_INDEX_NAME      = os.environ.get("AZURE_SEARCH_INDEX_NAME", "nda-mppr-projects")
SHEET_NAME             = os.environ.get("EXCEL_SHEET_NAME", "5a)NDA MPPR")
STORAGE_ACCOUNT_URL    = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "")
STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "nda-data")
EAC_BLOB_NAME          = os.environ.get("EAC_BLOB_NAME", "lifecycle_eac_variance.xlsx")


# ─────────────────────────────────────────────────────────────────────────────
# One credential instance reused across all calls (Managed Identity in cloud,
# az login when running locally).
# ─────────────────────────────────────────────────────────────────────────────
_credential = DefaultAzureCredential()


def ensure_index_exists() -> None:
    """Creates the Azure AI Search index if it does not already exist."""
    index_client = SearchIndexClient(
        endpoint=SEARCH_ENDPOINT,
        credential=_credential,
    )
    existing_names = [idx.name for idx in index_client.list_index_names()]
    if SEARCH_INDEX_NAME in existing_names:
        logger.info("Index '%s' already exists — skipping creation.", SEARCH_INDEX_NAME)
        return

    logger.info("Index '%s' not found — creating...", SEARCH_INDEX_NAME)
    fields = [
        SimpleField(name="id",                     type=SearchFieldDataType.String,  key=True,        filterable=True),
        SearchableField(name="ProjectName",         type=SearchFieldDataType.String,  filterable=True, sortable=True),
        SimpleField(name="ReportingPeriod",         type=SearchFieldDataType.String,  filterable=True, sortable=True, facetable=True),
        SimpleField(name="DCA_RAG_Status",          type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="DCA_RAG_Movement",        type=SearchFieldDataType.Int32,   filterable=True, sortable=True),
        SimpleField(name="Baseline_RAG_Status",     type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="Capability_Capacity_RAG", type=SearchFieldDataType.String,  filterable=True, facetable=True),
        SimpleField(name="BusinessCase_Cost_P50",   type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="BusinessCase_Cost_P80",   type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="EAC_Cost",                type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="EAC_vs_Last_Period",       type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Baseline_Cost_P50",       type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Timeline_Delay_Days",     type=SearchFieldDataType.Int32,   filterable=True, sortable=True),
        SimpleField(name="CPI",                     type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="SPI",                     type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SimpleField(name="Pct_Complete",            type=SearchFieldDataType.Double,  filterable=True, sortable=True),
        SearchableField(name="NarrativeText",       type=SearchFieldDataType.String,  analyzer_name="en.microsoft"),
    ]
    index = SearchIndex(name=SEARCH_INDEX_NAME, fields=fields)
    index_client.create_or_update_index(index)
    logger.info("Index '%s' created successfully.", SEARCH_INDEX_NAME)


def run_ingest(file_bytes: bytes, reporting_period: str) -> dict:
    """
    Parse the provided Excel file bytes and push all projects to Azure AI Search.
    Returns a summary dict with counts.
    """
    file_stream = io.BytesIO(file_bytes)
    projects = _extract_projects(file_stream, reporting_period)
    logger.info("Extracted %d projects from period %s.", len(projects), reporting_period)

    if not projects:
        return {
            "message": "No valid projects found in the uploaded file.",
            "reporting_period": reporting_period,
            "projects_extracted": 0,
            "documents_indexed": 0,
            "documents_failed": 0,
        }

    uploaded, failed = _push_to_search(projects)
    return {
        "message": "Processing complete.",
        "reporting_period": reporting_period,
        "projects_extracted": len(projects),
        "documents_indexed": uploaded,
        "documents_failed": failed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _make_id(name: str) -> str:
    """Create an Azure Search-safe document ID from a project name."""
    return (
        name
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("/", "_")
        .replace("&", "and")
        .replace(".", "")
        .replace(",", "")
    )[:128]


def _extract_projects(file_stream: io.BytesIO, reporting_period: str) -> list[dict]:
    """
    Reads the '5a)NDA MPPR' tab (columns A to AK) and returns a list of
    structured project dicts ready for AI Search indexing.
    """
    df = pd.read_excel(file_stream, sheet_name=SHEET_NAME, usecols="A:AK", header=None, dtype=str)
    df_typed = pd.read_excel(file_stream, sheet_name=SHEET_NAME, usecols="A:AK", header=None)

    projects = []

    for idx in range(len(df)):
        if idx < 17:  # Skip header rows
            continue

        col_A = df_typed.iat[idx, 0]
        col_B = df_typed.iat[idx, 1]

        b_str = _str(col_B)
        if b_str and "Table of Changes" in b_str:
            break

        # Metadata row: col A is NaN, col B is a short project name
        if _safe(col_A) is None and b_str and len(b_str) < 120:
            narrative = None
            if idx + 1 < len(df_typed):
                next_col_A = _str(df_typed.iat[idx + 1, 0])
                next_col_B = _str(df_typed.iat[idx + 1, 1])
                if next_col_A and next_col_A == b_str and next_col_B and len(next_col_B) > 120:
                    narrative = next_col_B.strip()

            if not narrative:
                continue

            doc = {
                "id":                     f"{_make_id(b_str)}_{reporting_period}",
                "ProjectName":            b_str,
                "ReportingPeriod":        reporting_period,
                "DCA_RAG_Status":         _str(df_typed.iat[idx, 3]),
                "DCA_RAG_Movement":       _int(df_typed.iat[idx, 4]),
                "Baseline_RAG_Status":    _str(df_typed.iat[idx, 14]),
                "Capability_Capacity_RAG":_str(df_typed.iat[idx, 36]),
                "BusinessCase_Cost_P50":  _float(df_typed.iat[idx, 7]),
                "BusinessCase_Cost_P80":  _float(df_typed.iat[idx, 8]),
                "EAC_Cost":               _float(df_typed.iat[idx, 23]),
                "EAC_vs_Last_Period":      _float(df_typed.iat[idx, 24]),
                "Baseline_Cost_P50":      _float(df_typed.iat[idx, 22]),
                "Timeline_Delay_Days":    _int(df_typed.iat[idx, 26]),
                "CPI":                    _float(df_typed.iat[idx, 31]),
                "SPI":                    _float(df_typed.iat[idx, 32]),
                "Pct_Complete":           _float(df_typed.iat[idx, 33]),
                "NarrativeText":          narrative,
            }
            projects.append(doc)

    return projects


def upload_eac_file(file_bytes: bytes) -> dict:
    """
    Upload the EAC variance Excel file to Azure Blob Storage.

    The file is stored as a single blob (overwriting any previous version).
    tools.py detects the new ETag on next call and automatically re-downloads
    the refreshed data — no redeployment needed.
    """
    if not STORAGE_ACCOUNT_URL:
        raise ValueError(
            "AZURE_STORAGE_ACCOUNT_URL is not set. "
            "Add it to local.settings.json (locally) or Function App Settings (Azure)."
        )

    blob_service = BlobServiceClient(
        account_url=STORAGE_ACCOUNT_URL,
        credential=_credential,
    )
    container_client = blob_service.get_container_client(STORAGE_CONTAINER_NAME)

    # Create the container if it doesn't exist yet (idempotent)
    try:
        container_client.create_container()
        logger.info("Created blob container '%s'.", STORAGE_CONTAINER_NAME)
    except Exception:
        pass  # Already exists — that's fine

    blob_client = container_client.get_blob_client(EAC_BLOB_NAME)
    blob_client.upload_blob(file_bytes, overwrite=True)
    logger.info("EAC variance file uploaded to blob '%s/%s'.", STORAGE_CONTAINER_NAME, EAC_BLOB_NAME)

    return {
        "message": "EAC variance file uploaded successfully.",
        "container": STORAGE_CONTAINER_NAME,
        "blob": EAC_BLOB_NAME,
    }


def _push_to_search(documents: list[dict]) -> tuple[int, int]:
    """
    Uploads project documents to Azure AI Search using mergeOrUpload
    (safe to re-run — updates existing records instead of duplicating).
    Returns (succeeded_count, failed_count).
    """
    client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX_NAME,
        credential=_credential,
    )
    BATCH_SIZE = 500
    succeeded = 0
    failed = 0

    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i: i + BATCH_SIZE]
        results = client.upload_documents(documents=batch)
        for result in results:
            if result.succeeded:
                succeeded += 1
            else:
                failed += 1
                logger.warning("Failed to index document '%s': %s", result.key, getattr(result, "error_message", "unknown"))

    return succeeded, failed
