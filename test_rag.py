"""
test_rag.py — Local test suite for rag_function before deploying to Azure.

Tests each layer of the pipeline in isolation:
    1. DB connection + schema check
    2. Embedder — single + batch embed
    3. Ingest — parse sample Excel + upsert to PGVector
    4. Validate — vector search + GPT structured response

Run:
    python test_rag.py

Reads credentials from rag_function/local.settings.json automatically.
No Azure Function host needed — tests modules directly.
"""

import json
import pathlib
import sys
import os

# ── Load local.settings.json ─────────────────────────────────────────────────
settings_path = pathlib.Path(__file__).parent / "rag_function" / "local.settings.json"
if settings_path.exists():
    with open(settings_path, encoding="utf-8") as f:
        for k, v in json.load(f).get("Values", {}).items():
            os.environ.setdefault(k, v)
    print(f"✅ Loaded credentials from {settings_path}\n")
else:
    print(f"⚠️  {settings_path} not found — using existing env vars\n")

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS = "✅"
FAIL = "❌"
SKIP = "⏭️ "

def header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def ok(msg: str):
    print(f"  {PASS} {msg}")

def fail(msg: str, exc: Exception):
    print(f"  {FAIL} {msg}: {exc}")

results = {}


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — DB Connection + Schema
# ─────────────────────────────────────────────────────────────────────────────
header("TEST 1 — PostgreSQL Connection & Schema")
try:
    from rag_function.db import DBConnection, ensure_schema

    ensure_schema()
    ok("ensure_schema() ran successfully")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM nda_projects;")
            count = cur.fetchone()[0]
            ok(f"nda_projects table exists — {count} rows currently")

            cur.execute("""
                SELECT indexname FROM pg_indexes WHERE tablename = 'nda_projects';
            """)
            idxs = [r[0] for r in cur.fetchall()]
            ok(f"Indexes present: {', '.join(idxs) or 'none yet'}")

    results["db"] = True
except Exception as e:
    fail("DB test failed", e)
    results["db"] = False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Embedder
# ─────────────────────────────────────────────────────────────────────────────
header("TEST 2 — Azure OpenAI Embedder")
try:
    from rag_function.embedder import embed, embed_batch

    sample_text = "The SRO DCA remains amber because of ongoing schedule risk."
    vec = embed(sample_text)
    expected_dims = int(os.environ.get("AZURE_OPENAI_EMBEDDING_DIMS", "3072"))
    assert len(vec) == expected_dims, f"Expected {expected_dims} dims, got {len(vec)}"
    ok(f"Single embed: {len(vec)} dims returned")

    batch_texts = [
        "P50 completion cost has been maintained in period.",
        "Action is being taken to mitigate schedule risk.",
        "Capability & Capacity RAG status is green.",
    ]
    vecs = embed_batch(batch_texts)
    assert len(vecs) == 3
    assert all(len(v) == expected_dims for v in vecs)
    ok(f"Batch embed: {len(vecs)} texts × {len(vecs[0])} dims")

    results["embedder"] = True
except Exception as e:
    fail("Embedder test failed", e)
    results["embedder"] = False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Ingest (uses a real Excel file if present)
# ─────────────────────────────────────────────────────────────────────────────
header("TEST 3 — Ingest Pipeline")

excel_candidates = list(pathlib.Path(".").glob("**/*.xlsx"))
nda_files = [f for f in excel_candidates if "P07" in f.name or "MPPR" in f.name.upper() or "NDA" in f.name.upper()]

if not nda_files:
    print(f"  {SKIP} No NDA Excel file found (looked for P07/MPPR/NDA in filenames)")
    print("       Place an Excel file in the repo root to test ingestion.")
    results["ingest"] = None
else:
    excel_path = nda_files[0]
    print(f"  Using: {excel_path}")
    try:
        from rag_function.ingest import run_ingest, parse_excel

        with open(excel_path, "rb") as f:
            file_bytes = f.read()

        # DEBUG — print what columns Excel actually has
        import io, pandas as pd
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        print(f"  Sheets: {xl.sheet_names}")
        nda_sheet = next((s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None)
        if nda_sheet:
            df_debug = pd.read_excel(xl, sheet_name=nda_sheet, header=2)
            print(f"  Columns (first 12): {list(df_debug.columns[:12])}")
            print(f"  Rows loaded: {len(df_debug)}")

        result = run_ingest(file_bytes)
        ok(f"Ingested {result['indexed']} projects for period {result.get('period','?')}")
        results["ingest"] = True
    except Exception as e:
        fail("Ingest pipeline failed", e)
        results["ingest"] = False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Validate (requires data in DB from Test 3)
# ─────────────────────────────────────────────────────────────────────────────
header("TEST 4 — Validation Pipeline (RAG + GPT)")

if results.get("db") and results.get("embedder"):
    SAMPLE_NARRATIVE = (
        "The SRO Delivery Confidence Assessment (DCA) remains Amber because a schedule "
        "review has identified a 57-day slip to the P50 Optimistic completion date. "
        "The first project benefit milestone is at risk. P50 completion cost has been "
        "maintained in period. Action is being taken to recover programme through "
        "resequencing of work packages. P50 schedule position has deteriorated in period "
        "due to resource constraints. The implications to contingency are being assessed. "
        "The Baseline RAG status against SL P50 Project Baseline is Amber due to schedule "
        "slippage. Highlights in period: design package submitted for approval. "
        "Capability & Capacity RAG status is Green due to stable resource levels."
    )

    try:
        from rag_function.validate import run_validate

        result = run_validate(
            narrative=SAMPLE_NARRATIVE,
            project_name="Security Systems Architecture",
            period="P07",
            top_k=3,
        )

        meta = result.get("_meta", {})
        ok(f"Chunks retrieved from PGVector: {meta.get('chunks_used', '?')}")
        ok(f"EAC flag: {meta.get('eac_flag', '?')} | variance: £{meta.get('eac_variance_m', 0):.3f}m")

        layer1 = result.get("layer1", {})
        layer2 = result.get("layer2", {})
        verdict = result.get("overall_verdict", "?")

        ok(f"Layer 1 compliance score: {layer1.get('compliance_score', '?')}/10")
        ok(f"Layer 2 — EAC explained: {layer2.get('eac_explained', '?')}")
        ok(f"Overall verdict: {verdict}")

        if "issues" in layer1 and layer1["issues"]:
            print(f"\n  Layer 1 issues flagged:")
            for issue in layer1["issues"][:5]:
                print(f"    • {issue}")

        if "suggestions" in result and result["suggestions"]:
            print(f"\n  Suggestions:")
            for s in result["suggestions"][:2]:
                print(f"    → {s}")

        results["validate"] = True
    except Exception as e:
        fail("Validation pipeline failed", e)
        results["validate"] = False
else:
    print(f"  {SKIP} Skipped — DB or embedder test failed")
    results["validate"] = None


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
header("SUMMARY")
status_map = {True: "✅ PASS", False: "❌ FAIL", None: "⏭️  SKIP"}
for test, status in results.items():
    print(f"  {test:<15} {status_map.get(status, '?')}")

fails = [k for k, v in results.items() if v is False]
if not fails:
    print("\n🎉 All tests passed — ready to deploy rag_function/ to Azure!")
else:
    print(f"\n⚠️  {len(fails)} test(s) failed: {', '.join(fails)}")
    print("   Fix the above errors before deploying.")
    sys.exit(1)
