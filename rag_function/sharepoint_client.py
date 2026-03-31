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


def _graph_get(path: str, token: str) -> Any:
    """GET a Microsoft Graph endpoint and return parsed JSON."""
    url = f"{_GRAPH_BASE}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _get_site_id(token: str) -> str:
    """Resolve the site URL to a Graph site ID, cached after first call."""
    global _cached_site_id
    if _cached_site_id:
        return _cached_site_id

    site_url = os.environ.get("SHAREPOINT_SITE_URL", "").strip()
    if not site_url:
        raise ValueError("SHAREPOINT_SITE_URL environment variable is not set.")

    data = _graph_get(f"/sites/{site_url}", token)
    _cached_site_id = data["id"]
    logger.info("sharepoint_client: resolved site '%s' → id=%s", site_url, _cached_site_id)
    return _cached_site_id


def _get_list_name() -> str:
    name = os.environ.get("SHAREPOINT_LIST_NAME", "").strip()
    if not name:
        raise ValueError("SHAREPOINT_LIST_NAME environment variable is not set.")
    return name


def list_files() -> List[Dict[str, str]]:
    """
    List Excel files from the configured SharePoint list.

    Returns a list of dicts:
        { file_id, name, last_modified, web_url }

    file_id is the list item ID — pass it back to download_file().
    """
    token     = _get_token()
    site_id   = _get_site_id(token)
    list_name = urllib.parse.quote(_get_list_name())

    # Expand driveItem to get file metadata; filter to Excel files only
    path = (
        f"/sites/{site_id}/lists/{list_name}/items"
        f"?$expand=driveItem($select=id,name,lastModifiedDateTime,webUrl,file)"
        f"&$select=id"
    )
    data  = _graph_get(path, token)
    items = data.get("value", [])

    files = []
    for item in items:
        drive_item = item.get("driveItem")
        if not drive_item:
            continue
        # Only include Excel files
        name = drive_item.get("name", "")
        if not (name.endswith(".xlsx") or name.endswith(".xls")):
            continue
        # Use driveItem id for downloading — it works with /drives/.../items/.../content
        files.append({
            "file_id":       drive_item["id"],
            "name":          name,
            "last_modified": drive_item.get("lastModifiedDateTime", ""),
            "web_url":       drive_item.get("webUrl", ""),
        })

    logger.info(
        "sharepoint_client: listed %d Excel file(s) from list '%s'",
        len(files), os.environ.get("SHAREPOINT_LIST_NAME", ""),
    )
    return files


def download_file(file_id: str) -> bytes:
    """
    Download a file from SharePoint by its driveItem ID.

    Returns raw bytes suitable for passing to list_projects_from_bytes().
    """
    if not file_id:
        raise ValueError("file_id must not be empty.")

    token   = _get_token()
    site_id = _get_site_id(token)

    # Get the drive associated with this list so we can use the drive download endpoint
    list_name = urllib.parse.quote(_get_list_name())
    list_data = _graph_get(f"/sites/{site_id}/lists/{list_name}?$select=id", token)
    list_id   = list_data["id"]

    # Get the drive for this list
    drives_data = _graph_get(f"/sites/{site_id}/lists/{list_id}/drive?$select=id", token)
    drive_id    = drives_data["id"]

    # Download file content — Graph follows the redirect automatically via urllib
    url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{file_id}/content"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    logger.info("sharepoint_client: downloaded %d bytes for item %s", len(data), file_id)
    return data