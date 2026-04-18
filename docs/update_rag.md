# Updating the RAG Knowledge Base

The AI trainer's `ask_trainer` feature is grounded in a cycling science knowledge
base stored in the `knowledge_chunks` PostgreSQL table.  There are two ways to
refresh it.

---

## Option 1 — HTTP endpoint (recommended for production)

Send an authenticated `POST` request to trigger ingestion as a background task.
The server responds immediately while the refresh runs asynchronously.

```bash
curl -X POST https://<host>/api/v1/ai/refresh-knowledge \
  -H "Authorization: Bearer <your-jwt-token>"
```

**Response**

```json
{ "status": "started", "message": "Knowledge base refresh has been queued and will run in the background" }
```

**Error conditions**

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid JWT token |
| `503`  | `OPENAI_API_KEY` is not set on the server |

---

## Option 2 — Run the ingestion script directly

Use this approach in local development or when running one-off data updates.

**Prerequisites**

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (pgvector must be installed) |
| `OPENAI_API_KEY` | ✅ | Used to generate `text-embedding-3-small` embeddings |
| `SEMANTIC_SCHOLAR_API_KEY` | ❌ | Optional — raises the rate limit from 1 req/s to 10 req/s |

**Run**

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/aitrainer \
OPENAI_API_KEY=sk-... \
python scripts/ingest_cycling_science.py
```

The script is fully **idempotent** — rows are upserted by `(source_id, chunk_index)`,
so re-running it will update existing chunks without creating duplicates.

---

## What gets ingested

### 1. Seed corpus — hand-written markdown files

All `*.md` files in `backend/knowledge/` are chunked (≈ 500-token chunks with
50-token overlap) and embedded.  Each file's stem becomes its `source_id`
(e.g. `seed:power_zones`).

To add new seed content, drop a `.md` file into `backend/knowledge/` and
re-run ingestion.

### 2. Semantic Scholar papers

The script queries the [Semantic Scholar](https://www.semanticscholar.org/) API
across a set of cycling-science topics and ingests the abstracts of open-access
papers.

Current queries (defined in `SEARCH_QUERIES` in `backend/scripts/ingest_cycling_science.py`):

```
cycling FTP lactate threshold power
polarized training endurance cycling
VO2max interval training cycling
training load recovery athlete
heart rate variability endurance athlete
sweet spot training cycling threshold
periodization endurance cycling performance
high intensity interval training VO2max
```

To add a new topic, append a query string to the `SEARCH_QUERIES` list and
re-run ingestion:

```python
# backend/scripts/ingest_cycling_science.py
SEARCH_QUERIES = [
    ...
    "altitude training cycling performance",   # new topic
]
```

---

## How retrieval works

At query time `services/rag.py` embeds the user's question with
`text-embedding-3-small` and performs a cosine-similarity search against the
`knowledge_chunks` table using pgvector.  The top-5 chunks are injected into the
`ask_trainer` system prompt, and their titles / DOIs / URLs are returned as
`sources` in the response.

On SQLite (used in tests) the RAG layer is skipped gracefully and returns an
empty context.
