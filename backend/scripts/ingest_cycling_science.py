"""Cycling science knowledge ingestion script.

Ingests two sources into the knowledge_chunks table:

1. Hand-written seed corpus — markdown files from backend/knowledge/.
2. Peer-reviewed papers — fetched from the Semantic Scholar API (open-access
   papers with abstracts for a set of cycling-science topics).

Usage:
    cd backend
    python scripts/ingest_cycling_science.py

Environment variables:
    DATABASE_URL            — required (PostgreSQL with pgvector)
    OPENAI_API_KEY          — required (text-embedding-3-small)
    SEMANTIC_SCHOLAR_API_KEY — optional; raises the API rate limit from
                               1 req/s (unauthenticated) to 10 req/s

The script is fully idempotent — it upserts by (source_id, chunk_index)
so it is safe to re-run without duplicating data.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://aitrainer:aitrainer@localhost/aitrainer"
)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Chunk parameters (measured in characters; tiktoken is used for a precise
# token count but characters give a reasonable approximation for chunking).
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
]

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "paperId,title,abstract,year,authors,externalIds,openAccessPdf"
PAPERS_PER_QUERY = 10

# ---------------------------------------------------------------------------
# Tokenisation helper (tiktoken)
# ---------------------------------------------------------------------------


def _token_count(text_input: str) -> int:
    """Count tokens in *text_input* using tiktoken (cl100k_base encoding)."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text_input))
    except Exception:
        # Rough fallback: 1 token ≈ 4 characters
        return len(text_input) // 4


def _chunk_text(text_input: str) -> list[str]:
    """Split *text_input* into overlapping chunks of ~CHUNK_SIZE_TOKENS tokens."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text_input)
    except Exception:
        # Fallback: treat every 4 characters as one token
        tokens = list(range(len(text_input)))  # dummy tokens for length
        # Simple character-based chunking
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

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        try:
            import tiktoken

            enc2 = tiktoken.get_encoding("cl100k_base")
            chunks.append(enc2.decode(chunk_tokens))
        except Exception:
            chunks.append(text_input[start * 4 : end * 4])
        if end == len(tokens):
            break
        start += CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def _embed_batch(texts: list[str], client: AsyncOpenAI) -> list[list[float]]:
    """Embed a batch of texts using OpenAI text-embedding-3-small."""
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    # Results are ordered by index
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


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
            vec_literal = "[" + ",".join(str(v) for v in chunk["embedding"]) + "]"
            sql = text(
                """
                INSERT INTO knowledge_chunks
                    (source_id, chunk_index, title, content, source_type, doi, url, embedding)
                VALUES
                    (:source_id, :chunk_index, :title, :content,
                     :source_type, :doi, :url, :embedding ::vector)
                ON CONFLICT (source_id, chunk_index)
                DO UPDATE SET
                    title       = EXCLUDED.title,
                    content     = EXCLUDED.content,
                    source_type = EXCLUDED.source_type,
                    doi         = EXCLUDED.doi,
                    url         = EXCLUDED.url,
                    embedding   = EXCLUDED.embedding
                """
            )
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
                    "embedding": vec_literal,
                },
            )
            inserted += 1
        await session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Seed corpus ingestion (markdown files)
# ---------------------------------------------------------------------------


async def ingest_seed_corpus(
    session_maker: async_sessionmaker,
    openai_client: AsyncOpenAI,
) -> int:
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
        content = md_path.read_text(encoding="utf-8")
        source_id = f"seed:{md_path.stem}"
        text_chunks = _chunk_text(content)

        logger.info("  %s → %d chunk(s)", md_path.name, len(text_chunks))

        embeddings = await _embed_batch(text_chunks, openai_client)
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


async def ingest_semantic_scholar(
    session_maker: async_sessionmaker,
    openai_client: AsyncOpenAI,
) -> int:
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
        embeddings = await _embed_batch(text_chunks, openai_client)

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
                    "embedding": embedding,
                }
            )
        n = await _upsert_chunks(session_maker, chunk_rows)
        total_inserted += n

    return total_inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for embedding generation")

    engine = create_async_engine(DATABASE_URL, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    logger.info("=== Cycling Science Ingestion ===")

    logger.info("Step 1: Seed corpus (knowledge/ markdown files)")
    seed_count = await ingest_seed_corpus(session_maker, openai_client)
    logger.info("  → %d seed chunks upserted", seed_count)

    logger.info("Step 2: Semantic Scholar papers")
    paper_count = await ingest_semantic_scholar(session_maker, openai_client)
    logger.info("  → %d paper chunks upserted", paper_count)

    logger.info("Total chunks upserted: %d", seed_count + paper_count)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
