"""Retrieval-Augmented Generation helper for cycling science.

Retrieves top-k relevant chunks from the knowledge_chunks table using
cosine similarity on pgvector embeddings, and returns a formatted context
string plus a sources list for citation.

Falls back to an empty result gracefully when:
- The database is not PostgreSQL (e.g. SQLite in tests)
- The pgvector extension is not installed
- The knowledge_chunks table is empty or missing
"""

from __future__ import annotations

import logging
import os
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _make_openai() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


async def _embed(text_input: str) -> list[float]:
    """Return the embedding vector for *text_input* using OpenAI."""
    client = _make_openai()
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text_input,
    )
    return response.data[0].embedding


async def retrieve_cycling_context(
    db: AsyncSession,
    query: str,
    k: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """Embed *query*, search knowledge_chunks by cosine similarity, and return
    a (context_string, sources_list) tuple.

    Returns ("", []) on any error so callers do not need special-case handling.
    """
    try:
        # Only works with PostgreSQL + pgvector.
        dialect = db.bind.dialect.name if db.bind else ""
        if dialect != "postgresql":
            return "", []

        embedding = await _embed(query)
        # Format as a pgvector literal string: '[0.1,0.2,…]'
        vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

        sql = text(
            """
            SELECT
                title,
                content,
                source_type,
                doi,
                url,
                1 - (embedding <=> :embedding ::vector) AS similarity
            FROM knowledge_chunks
            ORDER BY embedding <=> :embedding ::vector
            LIMIT :k
            """
        )
        result = await db.execute(sql, {"embedding": vec_literal, "k": k})
        rows = result.fetchall()

        if not rows:
            return "", []

        context_parts: list[str] = []
        sources: list[dict[str, Any]] = []

        for row in rows:
            title, content, source_type, doi, url, similarity = row
            context_parts.append(
                f"[Source: {title}]\n{content}"
            )
            source: dict[str, Any] = {"title": title, "sourceType": source_type}
            if doi:
                source["doi"] = doi
            if url:
                source["url"] = url
            source["similarity"] = round(float(similarity), 4)
            sources.append(source)

        context_str = "\n\n---\n\n".join(context_parts)
        return context_str, sources

    except Exception:
        logger.debug(
            "RAG retrieval failed (pgvector may not be available); returning empty context",
            exc_info=True,
        )
        return "", []
