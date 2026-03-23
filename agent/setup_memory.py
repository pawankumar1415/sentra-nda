"""
agent/setup_memory.py — One-time setup script for the Azure AI Foundry Memory Store.

Run this script ONCE to create the Memory Store that will be attached to the
nda-narrative-validator-v3 agent.  After creation the memory store persists in
your Azure AI Foundry project — you do not need to re-run this unless you want
to reset or recreate it.

Alternatively, you can create the Memory Store through the AI Foundry portal:
  AI Foundry Portal → Your Agent → Memory (Preview) → Add

What this script does
─────────────────────
1. Connects to Azure AI Foundry using DefaultAzureCredential (picks up `az login`
   locally or Managed Identity on Azure).
2. Creates a Memory Store named MEMORY_STORE_NAME (from config.py / env var).
3. Prints the store details so you can verify it in the portal.

Required environment variables (same as agent/config.py):
    AZURE_FOUNDRY_PROJECT_ENDPOINT
    AZURE_FOUNDRY_MODEL_DEPLOYMENT       (chat model — used for extraction)
    AZURE_FOUNDRY_EMBEDDING_DEPLOYMENT   (embedding model — used for retrieval)
    MEMORY_STORE_NAME                    (defaults to "nda-validation-memory")

SDK version required: azure-ai-projects >= 2.0.0 (stable)
    pip install "azure-ai-projects>=2.0.0"
"""

from __future__ import annotations

import os
import sys

# Add the agent directory to path when run directly
sys.path.insert(0, os.path.dirname(__file__))

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    MemoryStoreDefaultDefinition,
    MemoryStoreDefaultOptions,
)
from azure.identity import DefaultAzureCredential

from config import (
    PROJECT_ENDPOINT,
    MODEL_DEPLOYMENT_NAME,
    EMBEDDING_MODEL_DEPLOYMENT,
    MEMORY_STORE_NAME,
    MEMORY_UPDATE_DELAY_SECONDS,
)


def create_memory_store() -> None:
    """
    Create the NDA validation Memory Store in Azure AI Foundry.

    The Memory Store is configured to:
      - Summarise past validation sessions (chat_summary_enabled=True) so the
        agent can recall which projects were previously validated and what issues
        were found.
      - Track user preferences (user_profile_enabled=True) such as which projects
        a user most commonly validates and their preferred reporting periods.

    The `user_profile_details` string instructs the extraction LLM on what to
    capture and — importantly — what NOT to capture, reducing noise.
    """
    print(f"Connecting to Azure AI Foundry: {PROJECT_ENDPOINT}")

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    # Check whether the store already exists to make this script idempotent
    try:
        existing = client.beta.memory_stores.get(MEMORY_STORE_NAME)
        print(
            f"\nMemory store '{MEMORY_STORE_NAME}' already exists — no action taken."
            f"\n  Chat model:      {existing.definition.chat_model}"
            f"\n  Embedding model: {existing.definition.embedding_model}"
            "\nTo recreate it, delete it first via the portal or call "
            "client.beta.memory_stores.delete()."
        )
        return
    except Exception:
        # Store does not exist — proceed to create it
        pass

    print(f"\nCreating Memory Store '{MEMORY_STORE_NAME}'...")

    options = MemoryStoreDefaultOptions(
        # Summarise each conversation so the agent can recall past sessions:
        #   "Last session: validated Dounreay Shaft P07, found missing EAC sentence"
        chat_summary_enabled=True,

        # Track user-level preferences across sessions:
        #   "User frequently validates Dounreay Shaft and BEPPS2"
        #   "User prefers P07 period for their submissions"
        user_profile_enabled=True,

        # Instructions for the extraction LLM — tells it what to remember and
        # what to ignore. Be explicit to avoid storing sensitive/irrelevant data.
        user_profile_details=(
            "Remember: which NDA projects this user frequently validates, "
            "which reporting periods they commonly work with, "
            "recurring validation issues found in their narratives (e.g. missing EAC sentence), "
            "and any project-specific context they have explicitly provided. "
            "Do NOT store: verbatim narrative text, cost figures, or any PII."
        ),
    )

    definition = MemoryStoreDefaultDefinition(
        # Chat model: used to extract and consolidate memories after each session
        chat_model=MODEL_DEPLOYMENT_NAME,
        # Embedding model: used for semantic retrieval of relevant memories
        embedding_model=EMBEDDING_MODEL_DEPLOYMENT,
        options=options,
    )

    memory_store = client.beta.memory_stores.create(
        name=MEMORY_STORE_NAME,
        definition=definition,
        description=(
            "Long-term memory for the NDA Narrative Validation Agent. "
            "Extracts and retains validation history, project preferences, "
            "and recurring issues across sessions."
        ),
    )

    print(
        f"\nMemory Store created successfully!"
        f"\n  Name:            {memory_store.name}"
        f"\n  Chat model:      {memory_store.definition.chat_model}"
        f"\n  Embedding model: {memory_store.definition.embedding_model}"
        f"\n  Update delay:    {MEMORY_UPDATE_DELAY_SECONDS}s"
        "\n\nNext steps:"
        "\n  1. Go to AI Foundry Portal → nda-narrative-validator-v3 agent → Memory (Preview)"
        f"\n     and confirm '{MEMORY_STORE_NAME}' appears."
        "\n  2. Deploy the updated agent/agent_runner.py which uses this store."
        "\n  3. Set MEMORY_STORE_NAME in Azure Function App Settings if different from default."
    )


def delete_memory_store() -> None:
    """
    Delete the Memory Store (and all stored memories) from Azure AI Foundry.

    Use this to reset the memory store during development/testing.
    WARNING: This permanently deletes all extracted memories for all users.
    """
    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )

    confirm = input(
        f"\nThis will PERMANENTLY delete memory store '{MEMORY_STORE_NAME}' "
        "and ALL stored memories.\nType the store name to confirm: "
    )
    if confirm.strip() != MEMORY_STORE_NAME:
        print("Aborted — name did not match.")
        return

    client.beta.memory_stores.delete(MEMORY_STORE_NAME)
    print(f"Memory store '{MEMORY_STORE_NAME}' deleted.")


def list_memory_stores() -> None:
    """List all Memory Stores in the project."""
    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )
    stores = list(client.beta.memory_stores.list())
    if not stores:
        print("No memory stores found in this project.")
        return
    print(f"\nMemory stores in project ({len(stores)}):")
    for s in stores:
        print(f"  - {s.name}: {s.description or '(no description)'}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Manage the Azure AI Foundry Memory Store for the NDA agent."
    )
    parser.add_argument(
        "command",
        choices=["create", "delete", "list"],
        nargs="?",
        default="create",
        help="create (default), delete, or list memory stores",
    )
    args = parser.parse_args()

    if args.command == "create":
        create_memory_store()
    elif args.command == "delete":
        delete_memory_store()
    elif args.command == "list":
        list_memory_stores()
