"""Embedding provider abstraction for the cycling-science RAG.

``services.llm`` covers chat completions only, so RAG was hard-wired to OpenAI
(``text-embedding-3-small``).  Production runs Gemini-only with an empty
``OPENAI_API_KEY``, which meant retrieval could never embed a query and the
whole feature was dead while still costing a classification call per chat
message (#515).  This module mirrors ``llm.get_provider``: one place that
decides which provider embeds, so nothing above it branches on provider names.

Dimensions
----------
The stored ``knowledge_chunks.embedding`` column is a fixed-width pgvector, so
the corpus and the query *must* be embedded by the same model.  Every supported
model is therefore pinned to :data:`EMBEDDING_DIM` (768) — Gemini's embedding
models emit 3072 natively and support Matryoshka truncation, OpenAI's support
the ``dimensions`` parameter.  768 also keeps the vector under pgvector's
2000-dimension ceiling for an HNSW index.

Model names are configurable (``GEMINI_EMBEDDING_MODEL`` /
``OPENAI_EMBEDDING_MODEL``) so a retired model can be fixed by an env change
rather than a deploy — ``text-embedding-004``, which #515 proposed, had already
been withdrawn by the time this was written, the same failure mode as #401.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from config import settings

logger = logging.getLogger(__name__)

# Width of the pgvector column. Changing this requires a migration *and*
# re-running the ingestion — existing embeddings cannot be converted.
EMBEDDING_DIM = 768

# Gemini distinguishes the corpus side from the query side of a retrieval; using
# the matching task type on each is what the asymmetry is for.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"


class EmbeddingError(RuntimeError):
    """Raised when no embedding provider is configured or a provider fails."""


@runtime_checkable
class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], *, task_type: str) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or settings.gemini_embedding_model
        self._api_key = api_key or settings.gemini_api_key

    async def embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        async with client.aio as aio_client:
            response = await aio_client.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIM,
                ),
            )
        return [list(item.values) for item in response.embeddings]


class OpenAIEmbedder:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or settings.openai_embedding_model
        self._api_key = api_key or settings.openai_api_key

    async def embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        from openai import AsyncOpenAI

        # OpenAI has no query/document distinction; task_type is accepted and
        # ignored so callers stay provider-agnostic.
        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=EMBEDDING_DIM,
        )
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def get_embedder() -> Embedder:
    """Return the embedder for the configured provider.

    Follows the same precedence as ``llm._get_provider_global``: the explicitly
    configured provider first, then whichever key exists.  Raises rather than
    silently returning a provider with no key, because a failed embedding is
    indistinguishable from an empty corpus at the retrieval call site.
    """
    preferred = (settings.embedding_provider or "").strip().lower()
    if preferred == "gemini" and settings.gemini_api_key:
        return GeminiEmbedder()
    if preferred == "openai" and settings.openai_api_key:
        return OpenAIEmbedder()
    if settings.gemini_api_key:
        return GeminiEmbedder()
    if settings.openai_api_key:
        return OpenAIEmbedder()
    raise EmbeddingError(
        "No embedding provider configured; set GEMINI_API_KEY or OPENAI_API_KEY"
    )


def embedding_provider_is_configured() -> bool:
    """True when :func:`get_embedder` would return a usable embedder."""
    return bool(settings.gemini_api_key or settings.openai_api_key)


async def embed_query(query: str) -> list[float]:
    """Embed a single athlete question for retrieval."""
    vectors = await get_embedder().embed([query], task_type=TASK_QUERY)
    if not vectors:
        raise EmbeddingError("Embedding provider returned no vector for the query")
    return vectors[0]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed corpus chunks for storage."""
    if not texts:
        return []
    return await get_embedder().embed(texts, task_type=TASK_DOCUMENT)


def to_pgvector_literal(embedding: list[float]) -> str:
    """Render *embedding* as the ``'[0.1,0.2,…]'`` literal pgvector parses."""
    return "[" + ",".join(str(v) for v in embedding) + "]"
