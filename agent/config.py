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
EAC_VARIANCE_THRESHOLD_LOW: float   = 50_000       # Below this → no comment needed
EAC_VARIANCE_THRESHOLD_HIGH: float  = 100_000      # £0.1m+ → must appear in narrative
EAC_VARIANCE_THRESHOLD_MAJOR: float = 500_000      # £0.5m+ → needs explicit explanation

SCHEDULE_VARIANCE_THRESHOLD_DAYS: int = 0          # Any slip → must be mentioned

# ── Azure Blob Storage (EAC variance file) ─────────────────────────────────────
# The EAC variance Excel is stored in Blob Storage so it can be updated via the
# /api/ingest-eac endpoint without redeploying the function.
AZURE_STORAGE_ACCOUNT_URL: str = os.environ.get(
    "AZURE_STORAGE_ACCOUNT_URL",
    "",
)
AZURE_STORAGE_CONTAINER_NAME: str = os.environ.get(
    "AZURE_STORAGE_CONTAINER_NAME",
    "nda-data",
)
EAC_BLOB_NAME: str = os.environ.get(
    "EAC_BLOB_NAME",
    "lifecycle_eac_variance.xlsx",
)

# ── Foundry Memory Store (Preview) ────────────────────────────────────────────
# The Memory Store provides long-term, cross-session memory for the agent.
# It uses an LLM to extract and consolidate facts from conversations so the
# agent can recall past validation context, preferred projects, and recurring
# issues without them being re-stated in every session.
#
# Setup:
#   1. Run agent/setup_memory.py once to create the store, OR click "Add" under
#      "Memory (Preview)" in the AI Foundry portal for the nda-narrative-validator-v3 agent.
#   2. Set MEMORY_STORE_NAME in Azure Function App Settings to the store name.
#   3. Set AZURE_FOUNDRY_EMBEDDING_DEPLOYMENT to your embedding model deployment.
#
# The Memory Store requires TWO model deployments:
#   - Chat model  (MODEL_DEPLOYMENT_NAME above)     — used for extraction/consolidation
#   - Embedding model (AZURE_FOUNDRY_EMBEDDING_DEPLOYMENT) — used for memory retrieval

MEMORY_STORE_NAME: str = os.environ.get(
    "MEMORY_STORE_NAME",
    "MemoryStore-polite_cart_fhy4z4tbvx",
)

# Embedding model deployment used by the Memory Store for semantic retrieval.
# Found at: AI Foundry Portal → Deployments tab → embedding deployment name.
EMBEDDING_MODEL_DEPLOYMENT: str = os.environ.get(
    "AZURE_FOUNDRY_EMBEDDING_DEPLOYMENT",
    "text-embedding-3-small",
)

# ── Foundry Conversations (short-term session memory) ─────────────────────────
# The Conversations API holds raw message history within a single session.
# conversation_id is a durable Azure-managed UUID returned to the client on the
# first call and sent back on subsequent calls to resume the conversation.
# This complements the Memory Store: Conversations = this session's context,
# Memory Store = long-term facts extracted across all past sessions.
#
# No extra env vars needed — the Conversations API is part of get_openai_client().

# How long (seconds) of inactivity before the Memory Store writes extracted
# memories to long-term storage. 0 = write immediately after each turn.
# Default 300 (5 min) is suitable for production; use 0 in tests.
MEMORY_UPDATE_DELAY_SECONDS: int = int(
    os.environ.get("MEMORY_UPDATE_DELAY_SECONDS", "300")
)
