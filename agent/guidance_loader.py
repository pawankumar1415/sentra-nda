"""
agent/guidance_loader.py — Loads Good Practice guidance text from Azure Blob Storage.

Identical contract to rag_function/guidance_loader.py — both packages load from
the same blob so guidance stays in sync across both validation paths.

Required environment variables:
    AZURE_STORAGE_ACCOUNT_URL      e.g. https://mystorageaccount.blob.core.windows.net
    AZURE_STORAGE_CONTAINER_NAME   e.g. guidance
    GOOD_PRACTICE_BLOB_NAME        e.g. good_practice_guidance.docx

Falls back to embedded hardcoded guidance if any variable is missing or the
blob is unreachable, so the app degrades gracefully without breaking validation.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module-level etag cache ───────────────────────────────────────────────────
_cached_etag: Optional[str] = None
_cached_text: Optional[str] = None

# ── Fallback guidance ─────────────────────────────────────────────────────────
_FALLBACK_GUIDANCE = """
## NDA Narrative Good Practice Guidelines

When writing or validating an SRO project narrative, the following structure and rules apply:

### Required Sentence Templates (in order)
1. "The SRO / SPA DCA remains as / has changed / improved / deteriorated [to X] because [reason]."
2. "The first project benefit milestone (i.e., first package, operational readiness, transition) is [protected / at risk]."
3. "P50 completion cost has [increased by £Xm / been maintained in period] as a result of [reason]."
4. "Action is being taken [describe action]."
5. "P50 schedule position has [improved / deteriorated] in period due to [reason]."
6. "The implications of this to contingency, risk and resources are [explain]. Action being taken [describe] with the following opportunities being pursued [describe]."
7. "The Baseline RAG status against SL P50 Project Baseline is [RAG] due to [reason]; additionally (if applicable) this will change when [condition]."
8. "Highlights / issues in period: [describe]."
9. "Capability & Capacity RAG status is [RAG] due to [reason]."

### Mandatory Formatting Rules
- Narrative must read as a flowing paragraph, NOT in bullet points.
- All acronyms must be expanded on first use (e.g., "Delivery Confidence Assessment (DCA)").
- All dates must be written in full (e.g., "15th May 2024", NOT "May-24" or "05/24").
- Do not include building numbers (e.g., avoid "Building 204").
- Do not include document reference numbers.
- Any cost figures in the narrative must exactly match the figures from the reporting data.
- The narrative must be appropriate for an external/executive audience who may not know project detail.
- Where the project is part of GMPP, alignment must be maintained between messaging and RAGs.
- Comments must be at an "official" level of security — no sensitive operational detail.
"""


def _extract_text_from_docx(blob_bytes: bytes) -> str:
    """Extract plain text from a .docx file bytes using python-docx."""
    import docx  # python-docx
    doc = docx.Document(io.BytesIO(blob_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def get_guidance_text() -> tuple[str, str]:
    """
    Return (guidance_text, source) where source is 'blob' or 'fallback'.

    Checks the blob etag on every call but only re-downloads the content
    when the etag has changed, keeping warm-instance overhead to a minimum.
    """
    global _cached_etag, _cached_text

    account_url    = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    container_name = os.environ.get("GUIDANCE_AZURE_STORAGE_CONTAINER_NAME", "").strip()
    blob_name      = os.environ.get("GOOD_PRACTICE_BLOB_NAME", "").strip()

    if not (account_url and container_name and blob_name):
        logger.warning("guidance_loader: env vars not set — using fallback guidance")
        return _FALLBACK_GUIDANCE, "fallback"

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        credential  = DefaultAzureCredential()
        blob_client = BlobClient(
            account_url=account_url,
            container_name=container_name,
            blob_name=blob_name,
            credential=credential,
        )

        props        = blob_client.get_blob_properties()
        current_etag = props.etag

        if current_etag == _cached_etag and _cached_text is not None:
            logger.debug("guidance_loader: etag unchanged — returning cached text")
            return _cached_text, "blob"

        blob_data  = blob_client.download_blob().readall()
        text       = _extract_text_from_docx(blob_data)

        _cached_etag = current_etag
        _cached_text = text
        logger.info("guidance_loader: loaded %d chars from blob '%s'", len(text), blob_name)
        return text, "blob"

    except Exception as exc:
        logger.warning("guidance_loader: failed to load blob (%s) — using fallback", exc)
        return _FALLBACK_GUIDANCE, "fallback"