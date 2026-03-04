"""
debug_excel.py — Inspect the raw structure of the NDA MPPR Excel file.
Prints top 20 rows of the 5a)NDA MPPR sheet with no header parsing.

Run:
    venv\Scripts\python debug_excel.py
"""

import pathlib
import pandas as pd

# Find the Excel file
files = list(pathlib.Path("NDA Data").glob("P07*.xlsx"))
if not files:
    files = list(pathlib.Path(".").glob("**/P07*.xlsx"))

if not files:
    print("No P07 Excel file found")
    exit(1)

xl_path = files[0]
print(f"File: {xl_path}\n")

xl = pd.ExcelFile(xl_path)
print(f"Sheets: {xl.sheet_names}\n")

sheet = next((s for s in xl.sheet_names if "NDA MPPR" in s.upper()), None)
print(f"Using sheet: {sheet}\n")

# Read raw, no header — show first 20 rows and first 8 columns
df_raw = pd.read_excel(xl, sheet_name=sheet, header=None, nrows=20)
print("=== RAW ROWS (first 20 rows, first 8 cols) ===")
for i, row in df_raw.iterrows():
    vals = [str(v)[:25].replace("\n", "\\n") if not pd.isna(v) else "----" for v in row.iloc[:8]]
    print(f"  Row {i:2d}: {vals}")

print()

# Try reading with different header rows and show column names
for h in range(3, 8):
    df = pd.read_excel(xl, sheet_name=sheet, header=h, nrows=5)
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    named = [c for c in df.columns if not c.startswith("Unnamed")]
    print(f"header={h} → {len(named)} named columns: {named[:8]}")
