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
| `503`  | No embedding provider configured (`GEMINI_API_KEY` or `OPENAI_API_KEY`) |

---

## Option 2 — Run the ingestion script directly

Use this in local development or for one-off updates.

**Prerequisites**

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (pgvector must be installed) |
| `GEMINI_API_KEY` | ✅ | Unless embeddings are configured to use OpenAI |
| `OPENAI_API_KEY` | — | Alternative embedding provider |

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/aitrainer \
GEMINI_API_KEY=... \
python scripts/ingest_cycling_science.py
```

The script is **idempotent** — rows are upserted by `(source_id, chunk_index)`,
so re-running updates existing chunks without creating duplicates.

### `--retag-only`

```bash
python scripts/ingest_cycling_science.py --retag-only
```

Skips the ingest and only reconciles what is stored with what the code and the
files now say: prunes chunks no file produces any more, and brings every chunk's
topics up to date with the current vocabulary.  Costs no provider calls, so it
needs no embedding key and takes seconds.

Use it after changing `services/knowledge_topics.py`.  A plain re-ingest is not
enough on its own — the upsert only writes the rows it just built (#632).

---

## What gets ingested

Every `*.md` file in `backend/knowledge/` is chunked (≈500-token chunks, 50-token
overlap) and embedded.  The file stem becomes the `source_id`
(e.g. `seed:power_zones`).  To add knowledge, drop in a file and re-run.

**Excluding a file.** A file that documents the corpus rather than belonging to
it carries front matter:

```markdown
---
rag: false
---
```

`sources.md` — the bibliography — is excluded this way (#630).  A citation list
cannot answer a question, and it was being retrieved as though it could.

**Topic tags.** Each chunk is tagged by `services/knowledge_topics.py` with the
limiter(s) it speaks to (`threshold`, `vo2max`, `endurance_durability`), so
retrieval can rank by the athlete's diagnosed limiter (#627).  Most of the corpus
is deliberately untagged: a tag half the corpus carries cannot steer anything.

---

## Why there are no journal papers here

Until #640 the script also swept abstracts out of the Semantic Scholar API.  That
was removed for two reasons.

**Licence.** The S2 API permits "non-commercial, research and/or educational
purposes" only, and forbids licensees to commercialize the data.  This service is
free today and may not stay that way, and the stored abstracts are S2 data
whether or not an API key was ever issued.

**They were the weaker half.** Measured over ten representative questions, the
188 paper chunks took 23% of retrieved slots against the hand-written corpus's
77% — per chunk, about one eleventh the odds of ever being shown.  Of 78 freshly
ingested paper chunks only 15 earned a topic tag, and the rest included cupping
therapy, an ACE-polymorphism study and one materials-science paper.

Peer-reviewed work is meant to come back, but deliberately and from openly
licensed corpora — see #641.

---

## How retrieval works

`services/rag.py` embeds the question with the configured provider and runs a
cosine-similarity search against `knowledge_chunks` using pgvector.  Chunks below
`MIN_SIMILARITY` are dropped, so a question the corpus has nothing to say about
yields nothing rather than the five least-bad rows (#528).  Where the athlete has
a diagnosed limiter, chunks tagged with it are ranked above chunks that merely
resemble the question (#627).

The surviving top-k chunks are injected into the `ask_trainer` system prompt and
their titles/DOIs/URLs are returned as `sources`.

On SQLite (used in tests) the RAG layer is skipped gracefully and returns an
empty context.
