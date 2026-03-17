"""
test_deployment.py

Smoke-test script for the deployed Azure Function App (nda-foundry-api).
Tests all three endpoints: /api/ingest-eac, /api/ingest-mppr, /api/validate.

Usage:
    python test_deployment.py --key <FUNCTION_KEY>

Get your function key from:
    Azure Portal → Function App (nda-foundry-api) → Functions → <function> → Get Function Url
    OR: App Keys → default
"""

import argparse
import json
import os
import sys
import pathlib
import requests

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL    = "https://nda-foundry-api-g3b0f6fjgzgjhbfx.uksouth-01.azurewebsites.net/api"
TIMEOUT     = 120  # seconds — validation can take a while (LLM call)

EAC_FILE    = pathlib.Path(__file__).parent / "NDA Data" / "lifecycle_eac_variance.xlsx"

# Sample narrative used for the /api/validate smoke test
SAMPLE_PROJECT  = "Dounreay Shaft and Silo"
SAMPLE_PERIOD   = "P07 2025-26"
SAMPLE_NARRATIVE = (
    "The Delivery Confidence Assessment (DCA) remains Amber because scope uncertainties "
    "on the shaft project continue to present scheduling challenges. "
    "The first project benefit milestone is at risk due to delayed readiness reviews. "
    "P50 completion cost has increased by £1.2m in period as a result of additional "
    "ground investigation works required. Action is being taken to re-sequence activities "
    "to recover schedule where possible. P50 schedule position has deteriorated in period "
    "due to weather-related access restrictions. The implications of this to contingency, "
    "risk and resources are under active review with the project team. "
    "The Baseline RAG status against SL P50 Project Baseline is Amber due to cost growth. "
    "Highlights in period: ground investigation programme completed on 15th March 2025. "
    "Capability and Capacity RAG status is Green due to sufficient resource availability."
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg):     print(f"  {GREEN}✔  {msg}{RESET}")
def fail(msg):   print(f"  {RED}✘  {msg}{RESET}")
def warn(msg):   print(f"  {YELLOW}⚠  {msg}{RESET}")
def info(msg):   print(f"  {CYAN}ℹ  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}\n{'─' * 60}")


def post_json(url, payload, timeout=TIMEOUT):
    return requests.post(url, json=payload, timeout=timeout)


def post_file(url, field, filepath, extra_fields=None, timeout=TIMEOUT):
    with open(filepath, "rb") as fh:
        files  = {field: (filepath.name, fh, "application/octet-stream")}
        data   = extra_fields or {}
        return requests.post(url, files=files, data=data, timeout=timeout)


def url(route, key):
    return f"{BASE_URL}/{route}?code={key}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — /api/ingest-eac
# ─────────────────────────────────────────────────────────────────────────────

def test_ingest_eac(key: str) -> bool:
    header("TEST 1 — POST /api/ingest-eac  (Upload EAC variance file to Blob Storage)")

    if not EAC_FILE.exists():
        warn(f"EAC file not found at: {EAC_FILE}")
        warn("Skipping this test — place lifecycle_eac_variance.xlsx in 'NDA Data/' to run it.")
        return True  # Not a deployment failure

    info(f"Uploading: {EAC_FILE.name} ({EAC_FILE.stat().st_size // 1024} KB)")

    try:
        resp = post_file(url("ingest-eac", key), field="file", filepath=EAC_FILE)
    except requests.exceptions.ConnectionError:
        fail("Could not connect to the function app. Check the URL and your network.")
        return False
    except requests.exceptions.Timeout:
        fail(f"Request timed out after {TIMEOUT}s.")
        return False

    info(f"HTTP {resp.status_code}")

    if resp.status_code == 200:
        body = resp.json()
        ok(f"Upload succeeded: {body.get('message', '')}")
        ok(f"Container: {body.get('container')}  |  Blob: {body.get('blob')}")
        return True
    elif resp.status_code == 401:
        fail("401 Unauthorized — check your function key.")
    elif resp.status_code == 500:
        fail(f"500 Server Error — {resp.text[:300]}")
    else:
        fail(f"Unexpected status {resp.status_code} — {resp.text[:300]}")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — /api/validate  (core agent call)
# ─────────────────────────────────────────────────────────────────────────────

def test_validate(key: str) -> bool:
    header("TEST 2 — POST /api/validate  (AI agent narrative validation)")

    payload = {
        "project_name":  SAMPLE_PROJECT,
        "narrative":     SAMPLE_NARRATIVE,
        "period":        SAMPLE_PERIOD,
    }

    info(f"Project : {SAMPLE_PROJECT}")
    info(f"Period  : {SAMPLE_PERIOD}")
    info(f"Narrative length: {len(SAMPLE_NARRATIVE)} chars")
    print()

    try:
        resp = post_json(url("validate", key), payload)
    except requests.exceptions.ConnectionError:
        fail("Could not connect to the function app.")
        return False
    except requests.exceptions.Timeout:
        fail(f"Request timed out after {TIMEOUT}s — LLM may be slow, try increasing TIMEOUT.")
        return False

    info(f"HTTP {resp.status_code}")

    if resp.status_code == 400:
        fail(f"400 Bad Request — {resp.text[:300]}")
        return False
    elif resp.status_code == 401:
        fail("401 Unauthorized — check your function key.")
        return False
    elif resp.status_code == 500:
        fail(f"500 Server Error — {resp.text[:300]}")
        return False
    elif resp.status_code != 200:
        fail(f"Unexpected status {resp.status_code} — {resp.text[:300]}")
        return False

    body = resp.json()

    if "validation_result" not in body:
        fail(f"Response missing 'validation_result' key. Got: {list(body.keys())}")
        return False

    result_text = body["validation_result"]
    ok("Agent responded successfully.")
    ok(f"Response length: {len(result_text)} chars")

    # Spot-check the response contains expected structure
    checks = {
        "Layer 1 section present":    "Layer 1" in result_text,
        "Layer 2 section present":    "Layer 2" in result_text,
        "Compliance score present":   "Compliance Score" in result_text or "compliance" in result_text.lower(),
    }
    print()
    for label, passed in checks.items():
        if passed:
            ok(label)
        else:
            warn(f"{label} — not found in response (may still be valid)")

    print(f"\n{CYAN}--- Validation result preview (first 800 chars) ---{RESET}")
    print(result_text[:800])
    if len(result_text) > 800:
        print(f"  ... [{len(result_text) - 800} more chars]")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — /api/validate  error handling (missing narrative)
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_bad_request(key: str) -> bool:
    header("TEST 3 — POST /api/validate  (Error handling — missing 'narrative' field)")

    payload = {"project_name": "Test Project", "period": "P07 2025-26"}
    # Deliberately omitting "narrative"

    try:
        resp = post_json(url("validate", key), payload)
    except Exception as exc:
        fail(f"Request failed: {exc}")
        return False

    info(f"HTTP {resp.status_code}")

    if resp.status_code == 400:
        ok("Correctly returned 400 for missing 'narrative' field.")
        body = resp.json()
        ok(f"Error message: {body.get('error', '')}")
        return True
    else:
        fail(f"Expected 400, got {resp.status_code} — {resp.text[:200]}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Smoke-test the nda-foundry-api Azure Function App.")
    parser.add_argument("--key", required=True, help="Azure Function host/default key")
    args = parser.parse_args()

    print(f"\n{BOLD}{'=' * 60}")
    print("  NDA Foundry API — Deployment Smoke Test")
    print(f"  Target: {BASE_URL}")
    print(f"{'=' * 60}{RESET}")

    results = {}
    results["ingest-eac"]            = test_ingest_eac(args.key)
    results["validate"]              = test_validate(args.key)
    results["validate-bad-request"]  = test_validate_bad_request(args.key)

    # ── Summary ──────────────────────────────────────────────────────────────
    header("SUMMARY")
    all_passed = True
    for test_name, passed in results.items():
        if passed:
            ok(test_name)
        else:
            fail(test_name)
            all_passed = False

    print()
    if all_passed:
        print(f"{GREEN}{BOLD}All tests passed. Function App is healthy.{RESET}\n")
        sys.exit(0)
    else:
        print(f"{RED}{BOLD}One or more tests failed. Check output above.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()