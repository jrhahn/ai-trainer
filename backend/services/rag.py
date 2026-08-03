"""Retrieval-Augmented Generation helper for cycling science.

Retrieves top-k relevant chunks from the knowledge_chunks table using
cosine similarity on pgvector embeddings, and returns a formatted context
string plus a sources list for citation.

Embedding is delegated to ``services.embeddings`` so retrieval works under the
Gemini-only production config; it used to call OpenAI directly, which meant a
prod deployment with an empty ``OPENAI_API_KEY`` could never retrieve anything
(#515).

Falls back to an empty result gracefully when:
- The database is not PostgreSQL (e.g. SQLite in tests)
- The pgvector extension is not installed
- The knowledge_chunks table is empty or missing
- No embedding provider is configured
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.embeddings import embed_query, to_pgvector_literal

logger = logging.getLogger(__name__)

# Cached per process: the corpus is loaded by an ingestion run, not by request
# traffic, so this flips at most once in a process's lifetime. ``None`` means
# "not checked yet".
_corpus_populated: bool | None = None


def reset_corpus_cache() -> None:
    """Forget the cached corpus-presence answer (after an ingestion run, or in tests)."""
    global _corpus_populated
    _corpus_populated = None


async def _embed(text_input: str) -> list[float]:
    """Return the embedding vector for *text_input*."""
    return await embed_query(text_input)


async def knowledge_corpus_is_populated(db: AsyncSession) -> bool:
    """True when knowledge_chunks holds at least one row.

    Used to skip work that only makes sense against a real corpus. Cheap
    (``SELECT EXISTS`` with ``LIMIT 1``) and cached per process, so it costs one
    query per worker rather than one per request. Any error — missing table,
    non-PostgreSQL dialect — is reported as "not populated", which is the
    conservative answer: it disables retrieval rather than pretending it works.
    """
    global _corpus_populated
    if _corpus_populated is not None:
        return _corpus_populated

    try:
        dialect = db.bind.dialect.name if db.bind else ""
        if dialect != "postgresql":
            _corpus_populated = False
            return False
        result = await db.execute(
            text("SELECT EXISTS (SELECT 1 FROM knowledge_chunks LIMIT 1)")
        )
        _corpus_populated = bool(result.scalar())
    except Exception:
        logger.debug(
            "Could not determine whether the knowledge corpus is populated; "
            "treating it as empty",
            exc_info=True,
        )
        _corpus_populated = False

    if not _corpus_populated:
        logger.info(
            "Cycling-science corpus is empty; science retrieval is disabled. "
            "Run scripts/ingest_cycling_science.py to populate it."
        )
    return _corpus_populated


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
        vec_literal = to_pgvector_literal(embedding)

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
