"""
rag_function/embedder.py — Azure OpenAI embedding wrapper.

Calls text-embedding-3-small via the Azure OpenAI endpoint.
Returns float[1536] lists suitable for pgvector storage.

Required environment variables:
    AZURE_OPENAI_ENDPOINT                  e.g. https://myresource.openai.azure.com/
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT      e.g. text-embedding-3-small
    AZURE_OPENAI_API_VERSION               default: 2024-02-01
"""

from __future__ import annotations

import logging
import os
from typing import List

from openai import AzureOpenAI

logger = logging.getLogger(__name__)

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
    return _client


def _deployment() -> str:
    return os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")


def embed(text: str) -> List[float]:
    """Embed a single string. Returns float[1536]."""
    resp = _get_client().embeddings.create(
        model=_deployment(),
        input=text.replace("\n", " "),
    )
    return resp.data[0].embedding


def embed_batch(texts: List[str], batch_size: int = 16) -> List[List[float]]:
    """
    Embed a list of strings in batches (avoids rate-limit errors).
    Returns results in the same order as input.
    """
    client  = _get_client()
    model   = _deployment()
    results: List[List[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = [t.replace("\n", " ") for t in texts[i : i + batch_size]]
        resp  = client.embeddings.create(model=model, input=batch)
        vecs  = [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]
        results.extend(vecs)
        logger.debug("Embedded rows %d-%d", i, i + len(batch) - 1)

    return results
