"""
test_e2e.py — End-to-end test for the NDA RAG pipeline.

Tests the full flow against the deployed Azure Function:
  1. Upload P08 Excel file to /api/ingest
  2. Upload EAC data to /api/ingest-eac
  3. Validate a deliberately imperfect narrative via /api/validate
  4. Display full results for manual verification

Usage:
    python test_e2e.py --key YOUR_FUNCTION_KEY
"""

import argparse
import json
import pathlib
import sys
import urllib.request
import urllib.error
import urllib.parse

# ── Configuration ────────────────────────────────────────────────────────────
BASE_URL = (
    "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net"
)
DATA_DIR = pathlib.Path(__file__).parent / "NDA Data"

# ── The test narrative — deliberately imperfect so the validator catches issues ─
# This narrative is INTENTIONALLY flawed:
#   - Says DCA is "Green" (might not match actual P08 data → Layer 2 should flag)
#   - Missing: benefit milestone sentence, contingency/risk sentence,
#     baseline RAG sentence, highlights sentence → Layer 1 should flag
#   - Mentions an EAC figure (£1.2m) that may not match actual data → Layer 2 check
#   - Uses bullet point style → Layer 1 should flag
#   - Contains an unexpanded acronym "SL" → Layer 1 should flag
TEST_NARRATIVE = (
    "The SRO Delivery Confidence Assessment (DCA) is Green. "
    "The P50 completion cost is £1.2m. "
    "Schedule has slipped by 30 days due to supply chain issues. "
    "The Capability & Capacity RAG status is Green."
)

# We'll validate against a project that's likely in the P08 dataset.
# "Sellafield" is a major NDA project that should appear in the Excel.
TEST_PROJECT = "Sellafield"
TEST_PERIOD = "P08"


# ── Helpers ──────────────────────────────────────────────────────────────────
def api_call(route: str, key: str, payload=None, file_path=None) -> dict:
    """Make an API call to the deployed Azure Function."""
    url = f"{BASE_URL}/api/{route}"
    params = {}
    if key:
        params["code"] = key

    if file_path:
        # File upload
        params["filename"] = file_path.name
        url = f"{url}?{urllib.parse.urlencode(params)}"
        with open(file_path, "rb") as f:
            data = f.read()
        headers = {"Content-Type": "application/octet-stream"}
    else:
        # JSON payload
        url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def header(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="End-to-end RAG pipeline test")
    parser.add_argument("--key", type=str, required=True, help="Azure Function key")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip re-ingesting files (use if already ingested)")
    args = parser.parse_args()

    # ── Step 1: Ingest P08 ───────────────────────────────────────────────────
    if not args.skip_ingest:
        header("STEP 1 — Ingest P08 Excel")
        p08_path = DATA_DIR / "P08 Exec Project Summary FINAL.xlsx"
        if not p08_path.exists():
            print(f"  ❌ File not found: {p08_path}")
            sys.exit(1)

        print(f"  📤 Uploading {p08_path.name} ...")
        try:
            result = api_call("ingest", args.key, file_path=p08_path)
            print(f"  ✅ Period: {result.get('period')} | Projects indexed: {result.get('indexed')}")
        except urllib.error.HTTPError as e:
            print(f"  ❌ HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}")
            sys.exit(1)

        # Also ingest EAC data
        header("STEP 1b — Ingest EAC Variance Data")
        eac_path = DATA_DIR / "lifecycle_eac_variance.xlsx"
        if eac_path.exists():
            print(f"  📤 Uploading {eac_path.name} ...")
            try:
                result = api_call("ingest-eac", args.key, file_path=eac_path)
                print(f"  ✅ EAC records indexed: {result.get('indexed')}")
            except urllib.error.HTTPError as e:
                print(f"  ❌ HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}")
        else:
            print(f"  ⏭️  {eac_path.name} not found, skipping")
    else:
        print("\n  ⏭️  Skipping ingest (--skip-ingest flag)")

    # ── Step 2: Validate ─────────────────────────────────────────────────────
    header("STEP 2 — Validate Narrative")
    print(f"  Project:   {TEST_PROJECT}")
    print(f"  Period:    {TEST_PERIOD}")
    print(f"  Narrative: \"{TEST_NARRATIVE[:80]}...\"")
    print()
    print("  ⏳ Calling /api/validate (this may take 10-20s)...")

    try:
        result = api_call("validate", args.key, payload={
            "narrative": TEST_NARRATIVE,
            "project_name": TEST_PROJECT,
            "period": TEST_PERIOD,
            "top_k": 5,
        })
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}")
        sys.exit(1)

    # ── Step 3: Display Results ──────────────────────────────────────────────
    header("RESULTS — Layer 1 (Guidance & Structure)")
    layer1 = result.get("layer1", {})
    score = layer1.get("compliance_score", "?")
    print(f"  Compliance Score: {score}/10")

    issues = layer1.get("issues", [])
    if issues:
        print(f"\n  Issues Found ({len(issues)}):")
        for i, issue in enumerate(issues, 1):
            print(f"    {i}. {issue}")

    passed = layer1.get("passed", [])
    if passed:
        print(f"\n  Rules Passed ({len(passed)}):")
        for p in passed:
            print(f"    ✅ {p}")

    header("RESULTS — Layer 2 (Data Validation)")
    layer2 = result.get("layer2", {})
    print(f"  EAC Explained:      {layer2.get('eac_explained', '?')}")
    print(f"  Schedule Explained:  {layer2.get('schedule_explained', '?')}")
    print(f"  Data Flag:           {layer2.get('data_flag', '?')}")

    l2_issues = layer2.get("issues", [])
    if l2_issues:
        print(f"\n  Data Issues ({len(l2_issues)}):")
        for i, issue in enumerate(l2_issues, 1):
            print(f"    {i}. {issue}")

    header("RESULTS — Suggestions")
    suggestions = result.get("suggestions", [])
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s}")
    else:
        print("  (none)")

    header("OVERALL VERDICT")
    verdict = result.get("overall_verdict", "?")
    emoji = {"PASS": "✅", "PASS_WITH_WARNINGS": "⚠️", "FAIL": "❌"}.get(verdict, "❓")
    print(f"  {emoji} {verdict}")

    # ── Metadata ─────────────────────────────────────────────────────────────
    header("METADATA (what the RAG pipeline used)")
    meta = result.get("_meta", {})
    print(f"  Chunks retrieved:  {meta.get('chunks_used', '?')}")
    print(f"  EAC flag:          {meta.get('eac_flag', '?')}")
    print(f"  EAC variance:      £{meta.get('eac_variance_m', 0):.3f}m")
    print(f"  Schedule days:     {meta.get('schedule_days', '?')}")

    # ── Full JSON (for debugging) ────────────────────────────────────────────
    header("FULL JSON RESPONSE")
    print(json.dumps(result, indent=2))

    # ── Verification guide ───────────────────────────────────────────────────
    header("HOW TO VERIFY THIS IS CORRECT")
    print("""
  To verify the AI's response is accurate, open the P08 Excel file:
    NDA Data/P08 Exec Project Summary FINAL.xlsx

  1. Open the "5a)NDA MPPR" sheet
  2. Find the row for "{project}" (look in column B)
  3. Check these values against the METADATA above:
     - Column D  = DCA RAG status (R/A/G)
     - Column M  = Current P50 EAC (£m)
     - Column N  = EAC variance vs last period (£m)
     - Column P  = Schedule variance (days)
  4. The NEXT row should have the project's actual narrative text
     in column B — compare it with the Layer 1 issues

  Expected behavior for our TEST narrative:
    ✅ Layer 1 SHOULD flag issues because our test narrative is
       deliberately incomplete (missing benefit milestone, risk,
       baseline RAG, and highlights sentences)
    ✅ Layer 2 SHOULD check if the EAC/schedule figures we stated
       (£1.2m, 30 days) match the actual P08 data
    ✅ Verdict SHOULD be "FAIL" or "PASS_WITH_WARNINGS" because
       the test narrative is intentionally imperfect
""".format(project=TEST_PROJECT))


if __name__ == "__main__":
    main()
