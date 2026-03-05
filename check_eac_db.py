"""
check_eac_db.py — Quick script to check what's actually in the nda_eac_variance table.

Run this to see why "Sellafield" isn't matching in the validate endpoint.
It prints all project names and their EAC variances.
"""

import os
import json
import psycopg2
import pathlib

def main():
    # Load credentials from local.settings.json
    settings_path = pathlib.Path(__file__).parent / "rag_function" / "local.settings.json"
    with open(settings_path) as f:
        config = json.load(f)["Values"]
        
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(
        host=config["POSTGRES_HOST"],
        user=config["POSTGRES_USER"],
        password=config["POSTGRES_PASSWORD"],
        dbname=config["POSTGRES_DB"],
        sslmode="require"
    )
    
    with conn:
        with conn.cursor() as cur:
            # Check how many records we have
            cur.execute("SELECT COUNT(*) FROM nda_eac_variance;")
            count = cur.fetchone()[0]
            print(f"\nTotal records in nda_eac_variance: {count}")
            
            # Print all project names and their variances
            cur.execute("SELECT project_name, eac_variance, schedule_variance_days FROM nda_eac_variance ORDER BY project_name;")
            rows = cur.fetchall()
            
            print("\nProjects in database:")
            print("-" * 60)
            for name, eac, sched in rows:
                print(f"{name:<45} | EAC: £{eac:,.2f} | Sched: {sched}")
                
            # Test the exact query that validate.py uses
            print("\n" + "-" * 60)
            print("Testing validate.py query for project 'Sellafield':")
            cur.execute(
                "SELECT eac_variance, schedule_variance_days FROM nda_eac_variance WHERE lower(project_name) LIKE lower(%s)",
                ("%Sellafield%",)
            )
            match = cur.fetchone()
            if match:
                print(f"✅ MATCH FOUND! EAC: £{match[0]:,.2f}, Sched: {match[1]}")
            else:
                print("❌ NO MATCH FOUND for LIKE '%Sellafield%'")

if __name__ == "__main__":
    main()
