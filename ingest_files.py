"""
ingest_files.py — Bulk upload NDA reference files to the rag_function API.

Uploads P07, P08, P09 Excel files to /api/ingest, and
lifecycle_eac_variance.xlsx to /api/ingest-eac.

Usage:
    # Remote (deployed Azure Function):
    python ingest_files.py --remote --key YOUR_FUNCTION_KEY

    # Local (func host running on localhost):
    python ingest_files.py --local
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.request
import urllib.error
import urllib.parse

# ── Configuration ────────────────────────────────────────────────────────────
REMOTE_BASE = (
    "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net"
)
LOCAL_BASE = "http://localhost:7071"

DATA_DIR = pathlib.Path(__file__).parent / "NDA Data"

# Files to ingest via /api/ingest
MPPR_FILES = [
    "P07 Exec Project Summary FINAL.xlsx",
    "P08 Exec Project Summary FINAL.xlsx",
    "P09 Exec Project Summary FINAL.xlsx",
]

# Files to ingest via /api/ingest-eac
EAC_FILES = [
    "lifecycle_eac_variance.xlsx",
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def upload_file(base_url: str, route: str, file_path: pathlib.Path,
                function_key: str = "") -> dict:
    """
    Upload a file to the given Azure Function route using raw body + filename
    query parameter.
    """
    url = f"{base_url}/api/{route}"

    # Add filename as query parameter for period extraction
    params = {"filename": file_path.name}
    if function_key:
        params["code"] = function_key

    url = f"{url}?{urllib.parse.urlencode(params)}"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    req = urllib.request.Request(
        url=url,
        data=file_bytes,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Bulk ingest NDA files")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--remote", action="store_true", help="Use deployed Azure Function")
    group.add_argument("--local", action="store_true", help="Use local func host (localhost:7071)")
    parser.add_argument("--key", type=str, default="", help="Function key (required for --remote)")
    parser.add_argument("--data-dir", type=str, default=str(DATA_DIR),
                        help=f"Path to NDA Data folder (default: {DATA_DIR})")
    args = parser.parse_args()

    base_url = REMOTE_BASE if args.remote else LOCAL_BASE
    data_dir = pathlib.Path(args.data_dir)
    function_key = args.key

    if args.remote and not function_key:
        print("❌ --key is required when using --remote")
        sys.exit(1)

    if not data_dir.exists():
        print(f"❌ Data directory not found: {data_dir}")
        sys.exit(1)

    print(f"Target: {base_url}")
    print(f"Data dir: {data_dir}")
    print("=" * 60)

    # ── Ingest MPPR files ────────────────────────────────────────────────────
    for filename in MPPR_FILES:
        file_path = data_dir / filename
        if not file_path.exists():
            print(f"\n⏭️  SKIP — {filename} (file not found)")
            continue

        print(f"\n📤 Uploading {filename} to /api/ingest ...")
        try:
            result = upload_file(base_url, "ingest", file_path, function_key)
            status = result.get("status", "?")
            period = result.get("period", "?")
            indexed = result.get("indexed", "?")
            print(f"   ✅ {status} — period: {period}, indexed: {indexed} projects")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            print(f"   ❌ HTTP {e.code}: {body}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    # ── Ingest EAC files ─────────────────────────────────────────────────────
    for filename in EAC_FILES:
        file_path = data_dir / filename
        if not file_path.exists():
            print(f"\n⏭️  SKIP — {filename} (file not found)")
            continue

        print(f"\n📤 Uploading {filename} to /api/ingest-eac ...")
        try:
            result = upload_file(base_url, "ingest-eac", file_path, function_key)
            status = result.get("status", "?")
            indexed = result.get("indexed", "?")
            print(f"   ✅ {status} — indexed: {indexed} EAC records")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            print(f"   ❌ HTTP {e.code}: {body}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    print("\n" + "=" * 60)
    print("🎉 Bulk ingestion complete!")


if __name__ == "__main__":
    main()
