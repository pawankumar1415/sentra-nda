"""
rag_function/sharepoint_client.py — Microsoft Graph client for a SharePoint List.

Reads Excel files stored in a SharePoint List (e.g. SentraFileStaging).
Uses DefaultAzureCredential so the Function App managed identity is used in Azure
and the local credential chain (Azure CLI / env vars) is used during development.

Required environment variables:
    SHAREPOINT_SITE_URL    Hostname + site path, e.g. progserv.sharepoint.com:/sites/SellafieldDPMO
    SHAREPOINT_LIST_NAME   Display name of the SharePoint list,  e.g. SentraFileStaging

Required managed identity permission:
    Sites.Read.All   (Microsoft Graph application permission, admin consent required)
    — Sites.Read.All covers both site metadata and list item reads/downloads.
"""

from __future__ import annotations

import logging
import os
import urllib.request
import urllib.parse
import json
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_GRAPH_BASE  = "https://graph.microsoft.com/v1.0"

# Module-level cache for the resolved site ID (doesn't change between calls)
_cached_site_id: Optional[str] = None


def _get_token() -> str:
    """Acquire a Graph API access token via DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    return credential.get_token(_GRAPH_SCOPE).token


def _graph_get(url: str, token: str) -> Any:
    """GET a full Microsoft Graph URL and return parsed JSON."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _get_site_id(token: str) -> str:
    """
    Resolve SharePoint site to a Graph site ID, cached after first call.

    Checks SHAREPOINT_SITE_ID first (direct ID, skips lookup).
    Falls back to resolving via SHAREPOINT_SITE_URL using the
    hostname:/sites/path syntax required by Microsoft Graph.
    """
    global _cached_site_id
    if _cached_site_id:
        return _cached_site_id

    # Prefer direct site ID if provided — skips the lookup entirely
    direct_id = os.environ.get("SHAREPOINT_SITE_ID", "").strip()
    if direct_id:
        _cached_site_id = direct_id
        logger.info("sharepoint_client: using SHAREPOINT_SITE_ID directly: %s", direct_id)
        return _cached_site_id

    site_url = os.environ.get("SHAREPOINT_SITE_URL", "").strip()
    if not site_url:
        raise ValueError(
            "Either SHAREPOINT_SITE_ID or SHAREPOINT_SITE_URL environment variable must be set."
        )

    # Build the URL manually — urllib.parse.urlencode would encode the colon
    # in hostname:/path which breaks the Graph hostname:path syntax
    full_url = f"{_GRAPH_BASE}/sites/{site_url}"
    logger.info("sharepoint_client: resolving site via %s", full_url)
    data = _graph_get(full_url, token)
    _cached_site_id = data["id"]
    logger.info("sharepoint_client: resolved site '%s' → id=%s", site_url, _cached_site_id)
    return _cached_site_id


def _get_list_name() -> str:
    name = os.environ.get("SHAREPOINT_LIST_NAME", "").strip()
    if not name:
        raise ValueError("SHAREPOINT_LIST_NAME environment variable is not set.")
    return name


def _get_drive_id(site_id: str, token: str) -> str:
    """Get the drive ID for the configured SharePoint list/library."""
    list_name = urllib.parse.quote(_get_list_name())
    # Get the list's internal ID first
    list_data = _graph_get(
        f"{_GRAPH_BASE}/sites/{site_id}/lists/{list_name}?$select=id", token
    )
    list_id = list_data["id"]
    # Then get the drive attached to that list
    drive_data = _graph_get(
        f"{_GRAPH_BASE}/sites/{site_id}/lists/{list_id}/drive?$select=id", token
    )
    return drive_data["id"]


def list_files() -> List[Dict[str, str]]:
    """
    List Excel files from the configured SharePoint list/library.

    Returns a list of dicts:
        { file_id, name, last_modified, web_url }

    file_id is the drive item ID — pass it back to download_file().
    """
    token    = _get_token()
    site_id  = _get_site_id(token)
    drive_id = _get_drive_id(site_id, token)

    # List files from the drive root
    url  = f"{_GRAPH_BASE}/drives/{drive_id}/root/children?$select=id,name,lastModifiedDateTime,webUrl,file"
    data  = _graph_get(url, token)
    items = data.get("value", [])

    files = []
    for item in items:
        # Skip folders and non-Excel files
        if "file" not in item:
            continue
        name = item.get("name", "")
        if not (name.endswith(".xlsx") or name.endswith(".xls")):
            continue
        files.append({
            "file_id":       item["id"],
            "name":          name,
            "last_modified": item.get("lastModifiedDateTime", ""),
            "web_url":       item.get("webUrl", ""),
        })

    logger.info(
        "sharepoint_client: listed %d Excel file(s) from '%s'",
        len(files), os.environ.get("SHAREPOINT_LIST_NAME", ""),
    )
    return files


def download_file(file_id: str) -> bytes:
    """
    Download a file from SharePoint by its drive item ID.

    Returns raw bytes suitable for passing to list_projects_from_bytes().
    """
    if not file_id:
        raise ValueError("file_id must not be empty.")

    token    = _get_token()
    site_id  = _get_site_id(token)
    drive_id = _get_drive_id(site_id, token)

    url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{file_id}/content"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    logger.info("sharepoint_client: downloaded %d bytes for item %s", len(data), file_id)
    return data