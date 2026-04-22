import json
import math
import os
import sys

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
file_path = os.environ.get(
    "MPPR_EXCEL_PATH",
    os.path.join(_SCRIPT_DIR, "NDA Data", "P07 Exec Project Summary FINAL.xlsx"),
)
out_json_path = os.environ.get(
    "MPPR_OUTPUT_PATH",
    os.path.join(_SCRIPT_DIR, "mppr_cleaned_data.json"),
)

def is_nan(val):
    if isinstance(val, float) and math.isnan(val):
        return True
    if pd.isna(val):
        return True
    return False

def clean_data():
    print("Reading Excel file...")
    # Read the specific tab, skipping the first 16 rows of messy headers
    # We will use column indices manually since header names are split across rows
    df = pd.read_excel(file_path, sheet_name="5a)NDA MPPR", usecols="B:AK", skiprows=17, header=None)
    
    # We also read the first column (Column A) separately to get the narratives
    # Column A is index 0, B is 1
    df_with_A = pd.read_excel(file_path, sheet_name="5a)NDA MPPR", usecols="A:B", skiprows=17, header=None)
    
    projects = []
    
    # We will iterate through rows and look for project names in Column B (index 0 of df because we read B:AK)
    # Actually let's just read A:AK
    df_full = pd.read_excel(file_path, sheet_name="5a)NDA MPPR", usecols="A:AK", header=None)
    
    # The actual data seems to start around row 19 (0-indexed)
    # Let's find where the strings start
    
    current_project = None
    
    for idx, row in df_full.iterrows():
        if idx < 17:
            continue
            
        col_A = row[0]
        col_B = row[1]
        
        # If Column B has a string and Column A is NaN, it's a metadata row
        if is_nan(col_A) and not is_nan(col_B) and isinstance(col_B, str):
            # This might be a "Table of Changes" header
            if "Table of Changes" in col_B:
                break # Stop processing, we hit the bottom table
                
            current_project = {
                "id": f"{col_B.replace(' ', '_').replace(')', '').replace('(', '')}_P07",
                "ProjectName": col_B,
                "ReportingPeriod": "P07", # Hardcoded for this file
                "DCA_RAG_Status": row[3] if not is_nan(row[3]) else None,
                "Baseline_RAG_Status": row[14] if not is_nan(row[14]) else None,
                "Capability_Capacity_RAG": row[36] if not is_nan(row[36]) else None,
                "EAC_Cost": row[23] if not is_nan(row[23]) else None, # EAC Cost
                "CostVariance_Percentage": None, # Will calculate or extract
                "Timeline_Delay_Days": row[26] if not is_nan(row[26]) else 0, # Forecast vs Last period (Days)
                "NarrativeText": ""
            }
            
            # Look ahead to see if the next row has the narrative (in Col A or B)
            # Actually, in the CSV we saw:
            # Row 20: ,Project Name,,R...
            # Row 21: Project Name,"Narrative..." 
            # So in col A we have Project Name, and in col B we have Narrative text
            if idx + 1 < len(df_full):
                next_row = df_full.iloc[idx + 1]
                if not is_nan(next_row[0]) and next_row[0] == col_B:
                    # Found the narrative in next_row[1]
                    narrative = next_row[1]
                    if not is_nan(narrative) and isinstance(narrative, str):
                        current_project["NarrativeText"] = narrative
                        
            projects.append(current_project)

    # Filter out empty or invalid projects
    valid_projects = [p for p in projects if p["NarrativeText"] and p["ProjectName"]]
    
    print(f"Extracted {len(valid_projects)} valid projects.")
    
    with open(out_json_path, 'w', encoding='utf-8') as f:
        json.dump(valid_projects, f, indent=4)
        
    print(f"Successfully saved cleanly formatted JSON to {out_json_path}")
    
    # Print a sample for verification
    if valid_projects:
        print("\nSample Output:")
        print(json.dumps(valid_projects[0], indent=4))

if __name__ == "__main__":
    try:
        clean_data()
    except Exception as e:
        print(f"Error processing data: {e}")
