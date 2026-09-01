"""Cycling science knowledge ingestion script.

Ingests two sources into the knowledge_chunks table:

1. Hand-written seed corpus — markdown files from backend/knowledge/.
2. Peer-reviewed papers — fetched from the Semantic Scholar API (open-access
   papers with abstracts for a set of cycling-science topics).

Usage:
    cd backend
    python scripts/ingest_cycling_science.py
    python scripts/ingest_cycling_science.py --retag-only

``--retag-only`` skips both sources and just reconciles the stored corpus with
what the code and the knowledge/ files now say: it prunes seed chunks whose file
is gone or excluded, and brings every chunk's topics up to date with the current
vocabulary. It touches no provider, so it needs neither an embedding key nor the
Semantic Scholar API.

Environment variables:
    DATABASE_URL            — required (PostgreSQL with pgvector)
    GEMINI_API_KEY          — required unless embeddings are set to OpenAI
    OPENAI_API_KEY          — alternative embedding provider
    SEMANTIC_SCHOLAR_API_KEY — optional; raises the API rate limit from
                               1 req/s (unauthenticated) to 10 req/s

Embeddings go through ``services.embeddings``, which follows the configured
provider — the corpus must be embedded by the same model that embeds queries at
retrieval time, or the vectors are not comparable.

The script is fully idempotent — it upserts by (source_id, chunk_index)
so it is safe to re-run without duplicating data.

Maintenance rules:
- When adding a new knowledge file to backend/knowledge/, add the corresponding
  SEARCH_QUERIES entries below so that Semantic Scholar papers for that topic
  are also ingested.
- When citing a new paper in any knowledge file, add it to
  backend/knowledge/sources.md under the appropriate topic group. That file is
  bookkeeping, not corpus: it carries ``rag: false`` front matter and is not
  ingested (#630). Anything else in knowledge/ is corpus by default.
- Every chunk is tagged with ``services.knowledge_topics.topics_for_text`` so
  retrieval can rank by the athlete's limiter (#627). Changing that vocabulary
  means re-running this script: the tags live in the table, not in the query.
  A plain re-ingest is *not* enough on its own — the upsert only writes rows
  whose source it fetched, and the paper half is whatever Semantic Scholar
  returns that minute. That is what the re-tagging step at the end is for, and
  why it runs unconditionally (#632).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import sys

import httpx
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
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# Gemini's embedding endpoint caps a single request; batching keeps each call
# well inside that and inside the per-chunk input-token limit.
EMBED_BATCH_SIZE = 32

# Chunk parameters in tokens (cl100k_base encoding).
# When tiktoken is unavailable, a character-based approximation is used instead
# (1 token ≈ 4 characters), so these values also drive the character fallback.
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

SEARCH_QUERIES = [
    "cycling FTP lactate threshold power",
    "polarized training endurance cycling",
    "VO2max interval training cycling",
    "training load recovery athlete",
    "heart rate variability endurance athlete",
    "sweet spot training cycling threshold",
    "periodization endurance cycling performance",
    "high intensity interval training VO2max",
    # Critical power / power-duration model
    "critical power W-prime cycling Monod Scherrer",
    "power duration curve cycling anaerobic capacity",
    "W prime reconstitution severe intensity exercise",
    # Heat and altitude adaptation
    "heat acclimatisation cycling performance plasma volume",
    "altitude training live high train low haemoglobin",
    "heat stress endurance performance sauna post exercise",
    # Nutrition timing
    "carbohydrate intake during cycling performance fuelling",
    "multiple transportable carbohydrates fructose glucose oxidation",
    "post exercise nutrition glycogen resynthesis protein synthesis",
    "caffeine ergogenic aid cycling time trial",
    # Sleep and recovery
    "sleep extension athlete performance sprint reaction time",
    "sleep deprivation endurance performance RPE VO2max",
    "sleep quality recovery HRV growth hormone athlete",
    "napping daytime sleep performance endurance sport",
    "circadian rhythm chronotype athletic performance",
    # Tapering and peaking
    "taper training volume reduction endurance performance",
    "exponential taper cycling running performance meta-analysis",
    "pre-competition taper glycogen neuromuscular performance",
    "performance management chart CTL ATL TSB cycling",
    "carbohydrate loading pre-race glycogen supercompensation",
    # HRV-guided training
    "HRV-guided training autonomic nervous system endurance",
    "RMSSD daily monitoring training readiness athlete",
    "heart rate variability overreaching illness prediction",
    "parasympathetic nervous system recovery endurance training",
    # Strength training for endurance
    "concurrent strength endurance training interference effect",
    "heavy resistance training cycling economy running economy",
    "explosive strength training endurance performance neuromuscular",
    "resistance training VO2max lactate threshold cyclists",
    # HIIT and VO2max development
    "4x4 interval training VO2max endurance Helgerud",
    "short interval training VO2max development cycling",
    "sprint interval training aerobic capacity Gibala",
    "HIIT dose response endurance performance meta-analysis",
    # Athlete monitoring and load management
    "training load monitoring overtraining athlete RPE",
    "session RPE Foster internal training load validity",
    "acute chronic workload ratio injury risk sport",
    "athlete wellness questionnaire subjective readiness monitoring",
    # Masters athletes
    "masters athlete endurance performance age decline",
    "aging muscle power VO2max decline training older athlete",
    "masters cycling performance longevity training adaptations",
    # Triathlon and multisport
    "triathlon training periodization swim bike run",
    "brick training triathlon transition run economy",
    "multisport training load distribution triathlon performance",
]

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "paperId,title,abstract,year,authors,externalIds,openAccessPdf"
PAPERS_PER_QUERY = 10

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


async def prune_orphaned_seed_chunks(
    session_maker: async_sessionmaker, keep: set[str]
) -> int:
    """Delete ``seed:`` rows no longer produced by any knowledge file (#630).

    Only ever seed rows. The set of local markdown files is fully known on every
    run, so anything else under ``seed:`` is genuinely orphaned — a file that was
    renamed, deleted, or marked ``rag: false``. Papers must never be pruned this
    way: Semantic Scholar returns a partial, different subset each run, so
    "not fetched" says nothing about "no longer wanted" (#632).
    """
    async with session_maker() as session:
        rows = (
            await session.execute(
                text("SELECT DISTINCT source_id FROM knowledge_chunks WHERE source_id LIKE 'seed:%'")
            )
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
# Semantic Scholar ingestion
# ---------------------------------------------------------------------------


def _s2_paper_to_id(paper: dict[str, Any]) -> str:
    """Build a stable source_id for a Semantic Scholar paper."""
    doi = (paper.get("externalIds") or {}).get("DOI")
    if doi:
        return f"doi:{doi}"
    return f"s2:{paper['paperId']}"


async def _fetch_s2_papers(
    query: str, http_client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Fetch papers from Semantic Scholar for *query*."""
    headers: dict[str, str] = {}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    params = {
        "query": query,
        "fields": S2_FIELDS,
        "limit": PAPERS_PER_QUERY,
        "openAccessPdf": "true",
    }
    try:
        resp = await http_client.get(
            f"{S2_BASE_URL}/paper/search",
            params=params,
            headers=headers,
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
        logger.warning("S2 API returned %d for query '%s'", resp.status_code, query)
    except Exception as exc:
        logger.warning("S2 API request failed for query '%s': %s", query, exc)
    return []


async def ingest_semantic_scholar(session_maker: async_sessionmaker) -> int:
    """Query Semantic Scholar and ingest paper abstracts."""
    # Deduplicate papers across queries by source_id
    papers_by_id: dict[str, dict[str, Any]] = {}

    async with httpx.AsyncClient() as http_client:
        for query in SEARCH_QUERIES:
            logger.info("  Querying S2: %s", query)
            papers = await _fetch_s2_papers(query, http_client)
            for paper in papers:
                if not paper.get("abstract"):
                    continue
                sid = _s2_paper_to_id(paper)
                papers_by_id[sid] = paper

            # Respect the 1 req/s unauthenticated rate limit.
            rate_sleep = 0.2 if S2_API_KEY else 1.1
            await asyncio.sleep(rate_sleep)

    logger.info("  Found %d unique papers with abstracts", len(papers_by_id))
    if not papers_by_id:
        return 0

    total_inserted = 0
    for source_id, paper in papers_by_id.items():
        abstract = paper["abstract"]
        title = paper.get("title", "Unknown")
        doi = (paper.get("externalIds") or {}).get("DOI")
        pdf_info = paper.get("openAccessPdf") or {}
        url = pdf_info.get("url")

        text_chunks = _chunk_text(abstract)
        embeddings = await _embed_batch(text_chunks)

        chunk_rows: list[dict[str, Any]] = []
        for idx, (chunk_text, embedding) in enumerate(zip(text_chunks, embeddings)):
            chunk_rows.append(
                {
                    "source_id": source_id,
                    "chunk_index": idx,
                    "title": title,
                    "content": chunk_text,
                    "source_type": "paper",
                    "doi": doi,
                    "url": url,
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
        logger.info("  → %d seed chunks upserted", seed_count)

        logger.info("Step 2: Semantic Scholar papers")
        paper_count = await ingest_semantic_scholar(session_maker)
        logger.info("  → %d paper chunks upserted", paper_count)

        logger.info("Total chunks upserted: %d", seed_count + paper_count)

    # Both of the steps below reconcile the stored corpus with what the code and
    # the files now say, and neither costs a provider call — so they run on a
    # --retag-only pass too, where the upserts above are skipped entirely.

    # The upsert cannot remove: a file renamed, deleted, or newly marked
    # `rag: false` would otherwise stay retrievable forever (#630).  Guarded on
    # a non-empty set: an unreadable knowledge/ must not read as "delete the
    # whole seed corpus".
    keep = seed_source_ids()
    if keep:
        logger.info("Step 3: Pruning seed chunks no longer in knowledge/")
        pruned = await prune_orphaned_seed_chunks(session_maker, keep)
        logger.info("  → %d orphaned seed source(s) pruned", pruned)
    else:
        logger.warning("No readable knowledge/ files; skipping the prune")

    # The upserts only reached the sources this run actually fetched, so this is
    # the step that makes a vocabulary change reach the whole corpus rather than
    # a random subset of it (#632).
    logger.info("Step 4: Re-tagging the whole corpus")
    examined, rewritten = await retag_all_chunks(session_maker)
    logger.info("  → %d chunks examined, %d re-tagged", examined, rewritten)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(retag_only="--retag-only" in sys.argv[1:]))
