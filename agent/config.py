"""
agent/config.py — Centralised configuration for the AI Agent.

All values are read from environment variables (populated from local.settings.json
when running locally, or from Azure Function App Settings when deployed).
"""

import os


# ── Azure AI Foundry ───────────────────────────────────────────────────────────
# Found at: AI Foundry Portal → Your Project → Overview → "Project endpoint"
PROJECT_ENDPOINT: str = os.environ.get(
    "AZURE_FOUNDRY_PROJECT_ENDPOINT",
    "https://<YOUR_FOUNDRY_RESOURCE>.services.ai.azure.com/api/projects/<YOUR_PROJECT_NAME>",
)

# Found at: AI Foundry Portal → Deployments tab
MODEL_DEPLOYMENT_NAME: str = os.environ.get(
    "AZURE_FOUNDRY_MODEL_DEPLOYMENT",
    "gpt-4o",
)

# ── Azure AI Search (the connected resource) ───────────────────────────────────
# Found at: AI Foundry Portal → Management → Connected Resources → display name
AZURE_SEARCH_CONNECTION_NAME: str = os.environ.get(
    "AZURE_AI_SEARCH_CONNECTION_NAME",
    "<YOUR_AI_SEARCH_CONNECTION_NAME>",
)

AZURE_SEARCH_INDEX_NAME: str = os.environ.get(
    "AZURE_SEARCH_INDEX_NAME",
    "nda-mppr-projects",
)

# ── EAC Variance thresholds (agreed with Joanne) ──────────────────────────────
# NDA reports to £0.1m. Movements below £50k don't change reported values.
EAC_VARIANCE_THRESHOLD_LOW: float  = 50_000        # Below this → no comment needed
EAC_VARIANCE_THRESHOLD_HIGH: float = 100_000       # £0.1m+ → must appear in narrative
EAC_VARIANCE_THRESHOLD_MAJOR: float = 500_000      # £0.5m+ → needs explicit explanation

SCHEDULE_VARIANCE_THRESHOLD_DAYS: int = 0          # Any slip → must be mentioned

# ── Path to the EAC variance lookup file ──────────────────────────────────────
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
EAC_VARIANCE_FILE: pathlib.Path = REPO_ROOT / "NDA Data" / "lifecycle_eac_variance.xlsx"
