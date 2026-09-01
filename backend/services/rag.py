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

# Minimum cosine similarity a chunk must reach to be worth putting in front of
# the coach. Without a floor every question returned five chunks — ~2,400 tokens
# introduced as "relevant cycling science research" — however unrelated it was
# (#528).
#
# Calibrated against the production corpus (140 chunks, gemini-embedding-001 at
# 768 dimensions) over 12 questions: science questions peaked at 0.744–0.792,
# off-topic ones at 0.551–0.675. 0.70 sits in that gap. Note the populations
# only separate on the *best* hit — an off-topic question can beat the weakest
# kept chunk of a real one — so this drops trailing weak chunks on genuine
# questions too, which is the intended trade.
#
# Re-measure when the corpus or the embedding model changes; the numbers above
# are the baseline.
MIN_SIMILARITY = 0.70

# How much wider than *k* to search when the athlete has a diagnosed limiter
# (#627). Ranking can only promote a chunk it can see, and the plain top-k is by
# definition the set similarity alone already preferred — so without a wider pool
# the limiter could never change the answer. The floor below still applies to
# every extra row, so widening costs one larger LIMIT and nothing in the prompt.
FOCUS_CANDIDATE_MULTIPLIER = 3

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


def _addresses_focus(topics: Any, focus: set[str]) -> bool:
    """True when a chunk's tags overlap the topics the athlete is limited by."""
    if not focus or not topics:
        return False
    return bool(focus.intersection(topics))


async def retrieve_cycling_context(
    db: AsyncSession,
    query: str,
    k: int = 5,
    min_similarity: float | None = None,
    focus_topics: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Embed *query*, search knowledge_chunks by cosine similarity, and return
    a (context_string, sources_list) tuple.

    Chunks below *min_similarity* (default :data:`MIN_SIMILARITY`) are dropped,
    so a question the corpus has nothing to say about yields an empty context
    instead of the five least-bad rows.

    *focus_topics* are the topics the athlete's diagnosed limiter bears on (see
    :mod:`services.knowledge_topics`). Given them, retrieval searches a wider
    pool and ranks chunks tagged with one of those topics above chunks that only
    resemble the question, before truncating back to *k*. Two athletes asking the
    same question then get evidence about their own limiter rather than the same
    generic passage.

    The reordering is bounded on both sides. It cannot lift a chunk past the
    similarity floor — that gate stays absolute, so an off-topic question still
    returns nothing (#528) — and it cannot enlarge the prompt, because the result
    is still at most *k* chunks. With no focus topics, or against a corpus whose
    rows predate tagging, the result is byte-for-byte the pre-#627 behaviour.

    Returns ("", []) on any error so callers do not need special-case handling.
    """
    floor = MIN_SIMILARITY if min_similarity is None else min_similarity
    focus = {topic for topic in (focus_topics or []) if topic}
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
                1 - (embedding <=> :embedding ::vector) AS similarity,
                topics
            FROM knowledge_chunks
            ORDER BY embedding <=> :embedding ::vector
            LIMIT :k
            """
        )
        # Only pay for the wider pool when there is something to rank it by.
        limit = k * FOCUS_CANDIDATE_MULTIPLIER if focus else k
        result = await db.execute(sql, {"embedding": vec_literal, "k": limit})
        rows = result.fetchall()

        if not rows:
            return "", []

        # Filtered here rather than in SQL: a WHERE clause on the computed
        # distance would stop the HNSW index being used for the ORDER BY, and
        # this discards at most *limit* rows.
        kept = [row for row in rows if float(row[5]) >= floor]
        if focus:
            # Stable, so similarity order survives inside each group: the best
            # on-limiter chunk first, then the rest exactly as ranked before.
            kept.sort(key=lambda row: 0 if _addresses_focus(row[6], focus) else 1)
            kept = kept[:k]

        context_parts: list[str] = []
        sources: list[dict[str, Any]] = []

        for row in kept:
            title, content, source_type, doi, url, similarity, _topics = row
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

        if not context_parts:
            logger.debug(
                "No cycling-science chunk reached the %.2f similarity floor for %r",
                floor,
                query,
            )
            return "", []

        context_str = "\n\n---\n\n".join(context_parts)
        return context_str, sources

    except Exception:
        logger.debug(
            "RAG retrieval failed (pgvector may not be available); returning empty context",
            exc_info=True,
        )
        return "", []
