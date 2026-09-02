"""Cycling science knowledge ingestion script.

Ingests the hand-written seed corpus — the markdown files in backend/knowledge/ —
into the knowledge_chunks table.

That is the whole corpus since #640. It used to also sweep abstracts out of the
Semantic Scholar API, which was dropped for two reasons. The licence permits
non-commercial research and education only, and this service may not stay
non-commercial. And the papers were the weaker half regardless: measured over ten
representative questions they took 23% of retrieved slots against the seed
corpus's 77%, which per chunk is about one eleventh the odds of ever being shown.
Peer-reviewed work comes back deliberately and from openly licensed sources
(#641), not by sweeping fifty broad queries.

Usage:
    cd backend
    python scripts/ingest_cycling_science.py
    python scripts/ingest_cycling_science.py --retag-only

``--retag-only`` skips the ingest and just reconciles the stored corpus with what
the code and the knowledge/ files now say: it prunes chunks no file produces any
more, and brings every chunk's topics up to date with the current vocabulary. It
touches no provider, so it needs no embedding key.

Environment variables:
    DATABASE_URL   — required (PostgreSQL with pgvector)
    GEMINI_API_KEY — required unless embeddings are set to OpenAI
    OPENAI_API_KEY — alternative embedding provider

Embeddings go through ``services.embeddings``, which follows the configured
provider — the corpus must be embedded by the same model that embeds queries at
retrieval time, or the vectors are not comparable.

The script is fully idempotent — it upserts by (source_id, chunk_index)
so it is safe to re-run without duplicating data.

Maintenance rules:
- To add knowledge, drop a .md file into backend/knowledge/ and re-run this.
- When citing a new paper in any knowledge file, add it to
  backend/knowledge/sources.md under the appropriate topic group. That file is
  bookkeeping, not corpus: it carries ``rag: false`` front matter and is not
  ingested (#630). Anything else in knowledge/ is corpus by default.
- Every chunk is tagged with ``services.knowledge_topics.topics_for_text`` so
  retrieval can rank by the athlete's limiter (#627). Changing that vocabulary
  means re-running this script: the tags live in the table, not in the query.
  The upsert alone cannot do it — it only writes the rows it just built — which
  is what the re-tagging step at the end is for, and why it runs
  unconditionally (#632).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import sys

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Allow ``python scripts/ingest_cycling_science.py`` from backend/ to import the
# top-level app modules (services, config, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    embed_documents,
    embedding_provider_is_configured,
    get_embedder,
    to_pgvector_literal,
)
from services.knowledge_corpus import is_corpus_document, parse_front_matter  # noqa: E402
from services.knowledge_topics import (  # noqa: E402
    stale_chunk_tags,
    topics_for_text,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://aitrainer:aitrainer@localhost/aitrainer"
)
# Gemini's embedding endpoint caps a single request; batching keeps each call
# well inside that and inside the per-chunk input-token limit.
EMBED_BATCH_SIZE = 32

# Chunk parameters in tokens (cl100k_base encoding).
# When tiktoken is unavailable, a character-based approximation is used instead
# (1 token ≈ 4 characters), so these values also drive the character fallback.
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

# ---------------------------------------------------------------------------
# Tokenisation helper (tiktoken)
# ---------------------------------------------------------------------------


def _get_encoder():
    """Return the cl100k_base tiktoken encoder, or None if tiktoken is unavailable."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _token_count(text_input: str) -> int:
    """Count tokens in *text_input* using tiktoken (cl100k_base encoding)."""
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text_input))
    # Rough fallback: 1 token ≈ 4 characters
    return len(text_input) // 4


def _chunk_text(text_input: str) -> list[str]:
    """Split *text_input* into overlapping chunks of ~CHUNK_SIZE_TOKENS tokens."""
    enc = _get_encoder()

    if enc is None:
        # tiktoken unavailable: fall back to character-based chunking where
        # each character is treated as a "token" unit (1 token ≈ 4 characters).
        char_chunk = CHUNK_SIZE_TOKENS * 4
        char_overlap = CHUNK_OVERLAP_TOKENS * 4
        chunks = []
        start = 0
        while start < len(text_input):
            end = min(start + char_chunk, len(text_input))
            chunks.append(text_input[start:end])
            if end == len(text_input):
                break
            start += char_chunk - char_overlap
        return chunks

    tokens = enc.encode(text_input)
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed *texts* with the configured provider, in provider-sized batches."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(await embed_documents(texts[start : start + EMBED_BATCH_SIZE]))
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"Embedding provider returned {len(vectors)} vectors for {len(texts)} chunks"
        )
    return vectors


# ---------------------------------------------------------------------------
# Database upsert
# ---------------------------------------------------------------------------


async def _upsert_chunks(
    session_maker: async_sessionmaker,
    chunks: list[dict[str, Any]],
) -> int:
    """Upsert *chunks* into knowledge_chunks. Returns the number of rows inserted."""
    if not chunks:
        return 0

    async with session_maker() as session:
        inserted = 0
        for chunk in chunks:
            vec_literal = to_pgvector_literal(chunk["embedding"])
            sql = text(
                """
                INSERT INTO knowledge_chunks
                    (source_id, chunk_index, title, content, source_type, doi, url,
                     topics, embedding)
                VALUES
                    (:source_id, :chunk_index, :title, :content,
                     :source_type, :doi, :url, :topics, :embedding ::vector)
                ON CONFLICT (source_id, chunk_index)
                DO UPDATE SET
                    title       = EXCLUDED.title,
                    content     = EXCLUDED.content,
                    source_type = EXCLUDED.source_type,
                    doi         = EXCLUDED.doi,
                    url         = EXCLUDED.url,
                    topics      = EXCLUDED.topics,
                    embedding   = EXCLUDED.embedding
                """
            # The driver cannot infer text[] from a bare Python list, and an
            # untyped bind silently lands as a string the GIN index never matches.
            ).bindparams(bindparam("topics", type_=ARRAY(Text())))
            await session.execute(
                sql,
                {
                    "source_id": chunk["source_id"],
                    "chunk_index": chunk["chunk_index"],
                    "title": chunk["title"],
                    "content": chunk["content"],
                    "source_type": chunk["source_type"],
                    "doi": chunk.get("doi"),
                    "url": chunk.get("url"),
                    "topics": chunk.get("topics") or [],
                    "embedding": vec_literal,
                },
            )
            inserted += 1
        await session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Seed corpus ingestion (markdown files)
# ---------------------------------------------------------------------------


def seed_source_ids() -> set[str]:
    """The ``seed:`` ids the current knowledge/ files would produce.

    Reads the directory and nothing else — no embedding, no network — so the
    corpus can be reconciled with the files on a ``--retag-only`` run.
    """
    if not KNOWLEDGE_DIR.exists():
        return set()
    return {
        f"seed:{md_path.stem}"
        for md_path in sorted(KNOWLEDGE_DIR.glob("*.md"))
        if is_corpus_document(parse_front_matter(md_path.read_text(encoding="utf-8"))[0])
    }


async def prune_orphaned_chunks(session_maker: async_sessionmaker, keep: set[str]) -> int:
    """Delete every stored source no knowledge file produces any more (#630/#640).

    Scoped to ``seed:`` while a second, partially-fetched source existed: Semantic
    Scholar returned a different subset each run, so "not fetched this time" never
    meant "no longer wanted" (#632). With that source gone the local markdown
    files are the *entire* corpus and are fully known on every run, so anything
    else is orphaned by definition — including the paper rows #640 removes, which
    nothing will ever refresh again.

    Callers must pass a non-empty *keep*: an unreadable knowledge/ directory must
    not read as "delete the corpus".
    """
    if not keep:
        raise ValueError("refusing to prune against an empty corpus")

    async with session_maker() as session:
        rows = (
            await session.execute(text("SELECT DISTINCT source_id FROM knowledge_chunks"))
        ).fetchall()
        orphaned = [row[0] for row in rows if row[0] not in keep]
        for source_id in orphaned:
            await session.execute(
                text("DELETE FROM knowledge_chunks WHERE source_id = :source_id"),
                {"source_id": source_id},
            )
            logger.info("  pruned %s (no longer part of the corpus)", source_id)
        await session.commit()
    return len(orphaned)


async def ingest_seed_corpus(session_maker: async_sessionmaker) -> int:
    """Ingest the hand-written markdown files from backend/knowledge/."""
    if not KNOWLEDGE_DIR.exists():
        logger.warning("knowledge/ directory not found at %s; skipping seed corpus", KNOWLEDGE_DIR)
        return 0

    md_files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not md_files:
        logger.warning("No .md files found in %s", KNOWLEDGE_DIR)
        return 0

    total_inserted = 0
    for md_path in md_files:
        title = md_path.stem.replace("_", " ").title()
        front_matter, content = parse_front_matter(md_path.read_text(encoding="utf-8"))
        # Some files in knowledge/ document the corpus rather than belonging to
        # it — see services.knowledge_corpus (#630).
        if not is_corpus_document(front_matter):
            logger.info("  %s → skipped (marked rag: false)", md_path.name)
            continue
        source_id = f"seed:{md_path.stem}"
        text_chunks = _chunk_text(content)

        logger.info("  %s → %d chunk(s)", md_path.name, len(text_chunks))

        embeddings = await _embed_batch(text_chunks)
        chunk_rows: list[dict[str, Any]] = []
        for idx, (chunk_text, embedding) in enumerate(zip(text_chunks, embeddings)):
            chunk_rows.append(
                {
                    "source_id": source_id,
                    "chunk_index": idx,
                    "title": title,
                    "content": chunk_text,
                    "source_type": "seed",
                    "doi": None,
                    "url": None,
                    # Title included: a chunk from the middle of a durability
                    # article need not repeat the word to be about it (#627).
                    "topics": topics_for_text(title, chunk_text),
                    "embedding": embedding,
                }
            )
        n = await _upsert_chunks(session_maker, chunk_rows)
        total_inserted += n

    return total_inserted


# ---------------------------------------------------------------------------
# Re-tagging (#632)
# ---------------------------------------------------------------------------


async def retag_all_chunks(session_maker: async_sessionmaker) -> tuple[int, int]:
    """Bring every stored chunk's topics up to date with the current vocabulary.

    Returns ``(examined, rewritten)``. Costs no API calls at all — tagging reads
    only ``title`` and ``content``, which are already in the table — so this is
    safe to run on its own whenever the vocabulary changes.

    It has to exist because the upsert above cannot do it: that writes only rows
    whose source this run fetched, and the paper half is whatever Semantic
    Scholar returns that minute. Without this, a keyword added today reaches an
    arbitrary subset of the corpus and rows ingested before tagging existed stay
    ``NULL`` forever (#632).
    """
    async with session_maker() as session:
        rows = (
            await session.execute(
                text("SELECT id, title, content, topics FROM knowledge_chunks")
            )
        ).fetchall()

        stale = stale_chunk_tags((r[0], r[1], r[2], r[3]) for r in rows)
        update = text(
            "UPDATE knowledge_chunks SET topics = :topics WHERE id = :id"
        ).bindparams(bindparam("topics", type_=ARRAY(Text())))
        for chunk_id, topics in stale:
            await session.execute(update, {"id": chunk_id, "topics": topics})
        await session.commit()

    return len(rows), len(stale)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(retag_only: bool = False) -> None:
    # Re-tagging touches no provider, so it must not demand a provider key.
    if not retag_only and not embedding_provider_is_configured():
        raise RuntimeError(
            "No embedding provider configured; set GEMINI_API_KEY or OPENAI_API_KEY"
        )

    engine = create_async_engine(DATABASE_URL, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    if retag_only:
        logger.info("=== Re-tagging only (no fetching, no embedding) ===")
    else:
        logger.info("=== Cycling Science Ingestion ===")
        logger.info(
            "Embedding with %s (%d dimensions)", get_embedder().model, EMBEDDING_DIM
        )

        logger.info("Step 1: Seed corpus (knowledge/ markdown files)")
        seed_count = await ingest_seed_corpus(session_maker)
        logger.info("  → %d chunks upserted", seed_count)

    # Both of the steps below reconcile the stored corpus with what the code and
    # the files now say, and neither costs a provider call — so they run on a
    # --retag-only pass too, where the upsert above is skipped entirely.

    # The upsert cannot remove: a file renamed, deleted, or newly marked
    # `rag: false` would otherwise stay retrievable forever (#630), and so would
    # every Semantic Scholar row now that nothing fetches them (#640). Skipped
    # rather than run against an empty set: an unreadable knowledge/ must not
    # read as "delete the corpus".
    keep = seed_source_ids()
    if keep:
        logger.info("Step 2: Pruning chunks no longer in knowledge/")
        pruned = await prune_orphaned_chunks(session_maker, keep)
        logger.info("  → %d orphaned source(s) pruned", pruned)
    else:
        logger.warning("No readable knowledge/ files; skipping the prune")

    # The upsert only writes the rows it just built, so this is the step that
    # makes a vocabulary change reach the whole corpus (#632).
    logger.info("Step 3: Re-tagging the whole corpus")
    examined, rewritten = await retag_all_chunks(session_maker)
    logger.info("  → %d chunks examined, %d re-tagged", examined, rewritten)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(retag_only="--retag-only" in sys.argv[1:]))
