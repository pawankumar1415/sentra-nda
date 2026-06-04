"""
eval/diagnose_db.py — Database state diagnostic for both Custom and Foundry approaches.

Connects to the shared PostgreSQL database and runs comprehensive checks:
  1. Row counts and distinct project counts per period
  2. Duplicate detection (same project appearing multiple times per period)
  3. RAG status value distribution (checks for 'R'/'A'/'G' vs 'Red'/'Amber'/'Green')
  4. Missing project check (known Red projects that should exist)
  5. UNKNOWN period rows
  6. EAC variance table health
  7. Validation history summary
  8. Azure AI Search index stats (Foundry legacy index — optional)

Both the Custom approach (rag_function/) and the Foundry approach (agent/pgvector_backend.py)
share the same PostgreSQL database, so this script covers both.

Usage:
    cd eval
    # Option A: credentials already in environment
    python diagnose_db.py

    # Option B: credentials in eval/.env
    python diagnose_db.py

    # Option C: pass env file explicitly
    python diagnose_db.py --env path/to/.env

    # Skip Azure AI Search check (if not available):
    python diagnose_db.py --no-search

Output:
    Console report + eval/db_diagnostic_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

from dotenv import load_dotenv

HERE = pathlib.Path(__file__).parent


def _load_env(env_path: str | None) -> None:
    if env_path:
        p = pathlib.Path(env_path)
        if not p.exists():
            print(f"WARN: --env file not found: {p}")
        else:
            load_dotenv(p, override=True)
            print(f"Loaded env from {p}")
        return

    # Try eval/.env first, then parent local.settings.json values
    candidates = [
        HERE / ".env",
        HERE.parent / "rag_function" / "local.settings.json",
        HERE.parent / "agent" / "local.settings.json",
    ]
    for c in candidates:
        if c.exists():
            if c.suffix == ".json":
                _load_settings_json(c)
            else:
                load_dotenv(c, override=False)
            print(f"Loaded credentials from {c}")
            break


def _load_settings_json(path: pathlib.Path) -> None:
    """Extract Values dict from Azure Functions local.settings.json into os.environ."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.get("Values", {}).items():
            if k not in os.environ:
                os.environ[k] = str(v)
    except Exception as e:
        print(f"WARN: Could not parse {path}: {e}")


def _connect():
    """Return a psycopg2 connection to the PostgreSQL database."""
    host     = os.environ.get("POSTGRES_HOST", "")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB", "")
    user     = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    ssl      = os.environ.get("POSTGRES_SSL", "require")

    if not host or not db or not user:
        print("\nERROR: Missing PostgreSQL credentials.")
        print("  Set POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD")
        print("  in eval/.env or export them as environment variables.")
        sys.exit(1)

    # Azure AD token auth (when user contains '@' — Managed Identity / az login)
    if "@" in user and not password:
        try:
            from azure.identity import DefaultAzureCredential
            token = DefaultAzureCredential().get_token(
                "https://ossrdbms-aad.database.windows.net/.default"
            )
            password = token.token
            print("  Using Azure AD token for PostgreSQL auth.")
        except Exception as e:
            print(f"  WARN: Azure AD token failed: {e}. Trying without password.")

    import psycopg2
    return psycopg2.connect(
        host=host, port=port, dbname=db, user=user,
        password=password, sslmode=ssl
    )


# ── Diagnostic queries ─────────────────────────────────────────────────────────

def check_nda_projects(cur) -> dict:
    results = {}

    # 1. Total row count
    cur.execute("SELECT COUNT(*) FROM nda_projects")
    results["total_rows"] = cur.fetchone()[0]

    # 2. Rows per period
    cur.execute("""
        SELECT period_short_name, COUNT(*) AS row_count
        FROM nda_projects
        GROUP BY period_short_name
        ORDER BY period_short_name
    """)
    results["rows_per_period"] = [
        {"period": r[0], "row_count": r[1]} for r in cur.fetchall()
    ]

    # 3. Distinct projects per period
    cur.execute("""
        SELECT period_short_name, COUNT(DISTINCT project_name) AS distinct_projects
        FROM nda_projects
        GROUP BY period_short_name
        ORDER BY period_short_name
    """)
    results["distinct_projects_per_period"] = [
        {"period": r[0], "distinct_projects": r[1]} for r in cur.fetchall()
    ]

    # 4. Duplicates — projects appearing more than once in the same period
    cur.execute("""
        SELECT project_name, period_short_name, COUNT(*) AS occurrences
        FROM nda_projects
        GROUP BY project_name, period_short_name
        HAVING COUNT(*) > 1
        ORDER BY occurrences DESC, project_name
    """)
    rows = cur.fetchall()
    results["duplicates"] = [
        {"project_name": r[0], "period": r[1], "occurrences": r[2]} for r in rows
    ]
    results["duplicate_count"] = len(rows)

    # 5. RAG status value distribution (critical: should be R/A/G not Red/Amber/Green)
    cur.execute("""
        SELECT dca_rag_status, COUNT(*) AS count
        FROM nda_projects
        GROUP BY dca_rag_status
        ORDER BY count DESC
    """)
    results["rag_status_values"] = [
        {"value": r[0], "count": r[1]} for r in cur.fetchall()
    ]

    # 6. UNKNOWN period rows
    cur.execute("""
        SELECT COUNT(*) FROM nda_projects
        WHERE period_short_name = 'UNKNOWN' OR period_short_name IS NULL
    """)
    results["unknown_period_rows"] = cur.fetchone()[0]

    # 7. Check for known missing Red projects
    missing_projects = [
        "Calder Land Clearance",
        "Data Centre Replacement Project",
        "THORP Receipt & Storage Control Systems Replacement",
    ]
    missing_check = {}
    for name in missing_projects:
        cur.execute("""
            SELECT period_short_name, dca_rag_status, COUNT(*) AS cnt
            FROM nda_projects
            WHERE project_name ILIKE %s
            GROUP BY period_short_name, dca_rag_status
            ORDER BY period_short_name
        """, (f"%{name[:15]}%",))
        rows = cur.fetchall()
        missing_check[name] = [
            {"period": r[0], "rag": r[1], "count": r[2]} for r in rows
        ] if rows else "NOT FOUND IN DB"
    results["known_missing_projects"] = missing_check

    # 8. All distinct project names (to spot unexpected entries)
    cur.execute("""
        SELECT DISTINCT project_name
        FROM nda_projects
        ORDER BY project_name
    """)
    results["all_distinct_project_names"] = [r[0] for r in cur.fetchall()]

    # 9. Red projects in DB (should be 4 per period based on ground truth)
    cur.execute("""
        SELECT period_short_name, project_name, dca_rag_status
        FROM nda_projects
        WHERE dca_rag_status ILIKE '%r%'
          AND dca_rag_status NOT ILIKE '%green%'
          AND dca_rag_status NOT ILIKE '%amber%'
        ORDER BY period_short_name, project_name
    """)
    results["red_projects_in_db"] = [
        {"period": r[0], "project": r[1], "rag": r[2]} for r in cur.fetchall()
    ]

    # 10. Sample of project_id values to understand key format
    cur.execute("""
        SELECT project_id, project_name, period_short_name
        FROM nda_projects
        LIMIT 10
    """)
    results["sample_project_ids"] = [
        {"project_id": r[0], "project_name": r[1], "period": r[2]}
        for r in cur.fetchall()
    ]

    # 11. Most recent indexed_at timestamp
    cur.execute("SELECT MAX(indexed_at), MIN(indexed_at) FROM nda_projects")
    row = cur.fetchone()
    results["indexed_at_range"] = {
        "most_recent": row[0].isoformat() if row[0] else None,
        "oldest":      row[1].isoformat() if row[1] else None,
    }

    return results


def check_nda_eac_variance(cur) -> dict:
    results = {}

    cur.execute("SELECT COUNT(*) FROM nda_eac_variance")
    results["total_rows"] = cur.fetchone()[0]

    cur.execute("""
        SELECT flag, COUNT(*) AS cnt
        FROM nda_eac_variance
        GROUP BY flag
        ORDER BY cnt DESC
    """)
    results["flag_distribution"] = [
        {"flag": r[0], "count": r[1]} for r in cur.fetchall()
    ]

    cur.execute("""
        SELECT project_name, period_short_name, eac_variance, flag
        FROM nda_eac_variance
        ORDER BY abs(eac_variance) DESC
        LIMIT 10
    """)
    results["top_10_by_variance"] = [
        {"project": r[0], "period": r[1],
         "eac_variance_gbp": round(r[2], 2) if r[2] else 0, "flag": r[3]}
        for r in cur.fetchall()
    ]

    # Check for duplicate project names (primary key is project_name, so should be 0)
    cur.execute("""
        SELECT project_name, COUNT(*) FROM nda_eac_variance
        GROUP BY project_name HAVING COUNT(*) > 1
    """)
    results["duplicates"] = [r[0] for r in cur.fetchall()]

    return results


def check_validation_history(cur) -> dict:
    results = {}

    cur.execute("SELECT COUNT(*) FROM validation_history")
    results["total_rows"] = cur.fetchone()[0]

    if results["total_rows"] > 0:
        cur.execute("""
            SELECT overall_verdict, COUNT(*) AS cnt
            FROM validation_history
            GROUP BY overall_verdict
            ORDER BY cnt DESC
        """)
        results["verdict_distribution"] = [
            {"verdict": r[0], "count": r[1]} for r in cur.fetchall()
        ]

        cur.execute("SELECT MAX(validated_at), MIN(validated_at) FROM validation_history")
        row = cur.fetchone()
        results["date_range"] = {
            "most_recent": row[0].isoformat() if row[0] else None,
            "oldest":      row[1].isoformat() if row[1] else None,
        }

    return results


def check_chat_sessions(cur) -> dict:
    results = {}
    cur.execute("SELECT COUNT(*) FROM chat_sessions")
    results["total_sessions"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM chat_messages")
    results["total_messages"] = cur.fetchone()[0]
    return results


def check_azure_search(report: dict) -> None:
    """Optional: check Azure AI Search index stats for the Foundry legacy index."""
    endpoint   = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "nda-mppr-projects")

    if not endpoint:
        report["azure_search"] = {"skipped": True, "reason": "AZURE_SEARCH_ENDPOINT not set"}
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents import SearchClient

        cred         = DefaultAzureCredential()
        index_client = SearchIndexClient(endpoint=endpoint, credential=cred)
        search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=cred)

        # Doc count
        stats = index_client.get_index_statistics(index_name)
        doc_count    = stats.document_count
        storage_size = stats.storage_size

        # RAG status distribution via faceted search
        results_facet = list(search_client.search(
            search_text="*",
            facets=["DCA_RAG_Status,count:20", "ReportingPeriod,count:20"],
            top=0,
        ))

        report["azure_search"] = {
            "index_name":    index_name,
            "document_count": doc_count,
            "storage_bytes": storage_size,
            "facets": results_facet[0].get_facets() if results_facet else {},
        }
    except ImportError:
        report["azure_search"] = {
            "skipped": True,
            "reason":  "azure-search-documents not installed (pip install azure-search-documents)"
        }
    except Exception as e:
        report["azure_search"] = {"error": str(e)}


# ── Report printer ─────────────────────────────────────────────────────────────

def _sep(char="─", width=64):
    print(char * width)


def print_report(report: dict) -> None:
    _sep("═")
    print("  DATABASE DIAGNOSTIC REPORT")
    print(f"  Generated: {report['generated_at']}")
    _sep("═")

    pg = report.get("postgresql", {})

    # ── nda_projects ───────────────────────────────────────────────
    proj = pg.get("nda_projects", {})
    print(f"\n{'─'*20} nda_projects {'─'*20}")
    print(f"  Total rows:          {proj.get('total_rows', '?')}")
    print(f"  UNKNOWN period rows: {proj.get('unknown_period_rows', '?')}")

    print("\n  Rows per period:")
    for r in proj.get("rows_per_period", []):
        dp = next((x["distinct_projects"] for x in proj.get("distinct_projects_per_period", [])
                   if x["period"] == r["period"]), "?")
        dup_note = ""
        if r["row_count"] != dp:
            dup_note = f"  ⚠  {r['row_count'] - dp} EXTRA ROWS (duplicates?)"
        print(f"    {r['period']:<12}  rows={r['row_count']:<4}  distinct={dp:<4}{dup_note}")

    rag_vals = proj.get("rag_status_values", [])
    print("\n  RAG status values stored in DB:")
    for rv in rag_vals:
        flag = ""
        if rv["value"] in ("R", "A", "G"):
            flag = "  ✓ (correct single-letter format)"
        elif rv["value"].lower() in ("red", "amber", "green"):
            flag = "  ✗ WRONG — stored as full word, filter query will fail!"
        print(f"    '{rv['value']}'  ×{rv['count']}{flag}")

    dups = proj.get("duplicates", [])
    if dups:
        print(f"\n  ⚠  DUPLICATES FOUND: {len(dups)} project×period pairs with >1 row")
        for d in dups[:20]:
            print(f"    {d['project_name'][:50]:<50}  period={d['period']}  count={d['occurrences']}")
    else:
        print("\n  ✓ No duplicate project×period rows found.")

    print("\n  Known missing Red projects check:")
    for name, val in proj.get("known_missing_projects", {}).items():
        if val == "NOT FOUND IN DB":
            print(f"    ✗ NOT IN DB : {name}")
        else:
            print(f"    ✓ Found     : {name}")
            for entry in val:
                print(f"        period={entry['period']}  rag={entry['rag']}  rows={entry['count']}")

    red = proj.get("red_projects_in_db", [])
    print(f"\n  Red projects in DB ({len(red)} total rows):")
    if red:
        for r in red:
            print(f"    {r['period']:<8}  {r['rag']:<4}  {r['project']}")
    else:
        print("    (none found)")

    print(f"\n  All distinct project names ({len(proj.get('all_distinct_project_names', []))}):")
    for n in proj.get("all_distinct_project_names", []):
        print(f"    - {n}")

    print(f"\n  Indexed at: {proj.get('indexed_at_range', {})}")

    # ── nda_eac_variance ──────────────────────────────────────────
    eac = pg.get("nda_eac_variance", {})
    print(f"\n{'─'*20} nda_eac_variance {'─'*17}")
    print(f"  Total rows: {eac.get('total_rows', '?')}")
    if eac.get("duplicates"):
        print(f"  ⚠  Duplicate project names: {eac['duplicates']}")
    print("  Flag distribution:")
    for f in eac.get("flag_distribution", []):
        print(f"    {f['flag']:<12}  ×{f['count']}")
    print("  Top 10 by absolute variance:")
    for r in eac.get("top_10_by_variance", []):
        print(f"    £{r['eac_variance_gbp']/1_000_000:>8.3f}m  {r['flag']:<10}  {r['project']}")

    # ── validation_history ────────────────────────────────────────
    vh = pg.get("validation_history", {})
    print(f"\n{'─'*20} validation_history {'─'*15}")
    print(f"  Total rows: {vh.get('total_rows', '?')}")
    if vh.get("verdict_distribution"):
        for v in vh["verdict_distribution"]:
            print(f"    {v['verdict']:<25} ×{v['count']}")
    if vh.get("date_range"):
        print(f"  Date range: {vh['date_range']['oldest']} → {vh['date_range']['most_recent']}")

    # ── chat ──────────────────────────────────────────────────────
    chat = pg.get("chat", {})
    print(f"\n{'─'*20} chat {'─'*33}")
    print(f"  Sessions : {chat.get('total_sessions', '?')}")
    print(f"  Messages : {chat.get('total_messages', '?')}")

    # ── Azure Search ──────────────────────────────────────────────
    az = report.get("azure_search", {})
    if az:
        print(f"\n{'─'*20} Azure AI Search (Foundry legacy) {'─'*6}")
        if az.get("skipped"):
            print(f"  Skipped: {az['reason']}")
        elif az.get("error"):
            print(f"  Error: {az['error']}")
        else:
            print(f"  Index:    {az.get('index_name')}")
            print(f"  Docs:     {az.get('document_count')}")
            print(f"  Storage:  {az.get('storage_bytes', 0) / 1024:.1f} KB")

    _sep("═")
    print("  DIAGNOSIS SUMMARY")
    _sep("─")

    issues = []
    proj = report.get("postgresql", {}).get("nda_projects", {})

    rag_vals = {r["value"]: r["count"] for r in proj.get("rag_status_values", [])}
    full_words = {k: v for k, v in rag_vals.items()
                  if k.lower() in ("red", "amber", "green", "r", "a", "g")}
    bad_vals = [k for k in full_words if k.lower() in ("red", "amber", "green")]
    if bad_vals:
        issues.append(f"RAG status stored as full words {bad_vals} — "
                      "filter query uses ILIKE '%Red%' which will NEVER match single letters 'R'/'A'/'G'")

    if proj.get("duplicate_count", 0) > 0:
        issues.append(f"{proj['duplicate_count']} duplicate project×period rows — "
                      "portfolio summary returns every project multiple times")

    missing = proj.get("known_missing_projects", {})
    not_found = [k for k, v in missing.items() if v == "NOT FOUND IN DB"]
    if not_found:
        issues.append(f"Missing Red projects: {not_found}")

    if proj.get("unknown_period_rows", 0) > 0:
        issues.append(f"{proj['unknown_period_rows']} rows with UNKNOWN period — "
                      "period detection failed during ingest")

    if issues:
        print(f"\n  Found {len(issues)} issue(s):\n")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}\n")
    else:
        print("\n  No critical issues found.\n")

    _sep("═")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose DB state for NDA eval pipeline.")
    parser.add_argument("--env",       help="Path to .env file (default: auto-detect)")
    parser.add_argument("--no-search", action="store_true",
                        help="Skip Azure AI Search check")
    args = parser.parse_args()

    _load_env(args.env)

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "postgresql":   {},
        "azure_search": {},
    }

    print("\nConnecting to PostgreSQL...")
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    try:
        conn = _connect()
        print("  Connected.\n")
    except Exception as e:
        print(f"  Connection failed: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            print("Checking nda_projects...")
            report["postgresql"]["nda_projects"] = check_nda_projects(cur)

            print("Checking nda_eac_variance...")
            report["postgresql"]["nda_eac_variance"] = check_nda_eac_variance(cur)

            print("Checking validation_history...")
            report["postgresql"]["validation_history"] = check_validation_history(cur)

            print("Checking chat tables...")
            report["postgresql"]["chat"] = check_chat_sessions(cur)

    conn.close()

    if not args.no_search:
        print("Checking Azure AI Search (Foundry legacy index)...")
        check_azure_search(report)

    print_report(report)

    out_path = HERE / "db_diagnostic_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nFull report saved → {out_path}\n")


if __name__ == "__main__":
    main()