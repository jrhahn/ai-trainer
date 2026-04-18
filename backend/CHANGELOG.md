# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-04-18

### Fixed

- **Alembic migration chain** — `20260418_000001_add_knowledge_chunks` had `down_revision = "20260414_000001"` which branched off the middle of the chain and created two heads, causing `alembic upgrade head` to fail with *"Multiple head revisions are present"*. Corrected `down_revision` to `"20260416_000001"` (the actual latest head at the time), restoring a linear single-head chain.

### Added

- **Migration chain integrity tests** (`backend/tests/test_migrations.py`) — 4 static tests using `alembic.script.ScriptDirectory` (no live database required) that guard against the class of regression fixed above:
  - Exactly one head revision exists
  - Migration chain is linear (no branching / no revision with multiple parents)
  - All `down_revision` values reference revisions that actually exist
  - All revision IDs are unique

## [0.3.0] - 2026-04-18

### Added

- **Cycling science RAG** — retrieval-augmented generation layer that grounds `ask_trainer` responses in peer-reviewed cycling science:
  - `services/rag.py`: `retrieve_cycling_context(db, query, k=5)` embeds the athlete's question with `text-embedding-3-small` and runs cosine-similarity search against the `knowledge_chunks` table using pgvector. Returns `("", [])` gracefully when pgvector is unavailable (e.g. SQLite in tests).
  - `/ai/ask-trainer` now calls `retrieve_cycling_context` on every request and injects the retrieved science context into the system prompt; retrieved source metadata is returned in the response as `sources`.
  - `AskTrainerResponse` schema gains an optional `sources: list` field so callers can surface citations.
  - `services/prompts.py`: `ask_trainer_system` accepts a `science_context` parameter; the system prompt instructs the AI to cite retrieved sources in its JSON response.
  - `services/ai_service.ask_trainer` accepts a `science_context` parameter and returns `sources[]` in the result dict.
- **`knowledge_chunks` table** (Alembic migration `20260418_000001`):
  - Columns: `id`, `source_id`, `chunk_index`, `title`, `content`, `source_type`, `doi`, `url`, `embedding vector(1536)`, `created_at`.
  - Unique constraint on `(source_id, chunk_index)` for idempotent upserts.
  - HNSW index (`vector_cosine_ops`) for fast approximate nearest-neighbour search.
  - SQLite fallback (stores embedding as text) so the test suite continues to run without PostgreSQL.
- **pgvector Postgres image** — `compose.yml` now uses `pgvector/pgvector:pg17` instead of `postgres:17-alpine`; no new service is added.
- **Seed knowledge corpus** (`backend/knowledge/`) — five hand-written markdown files covering the required cycling science topics:
  - `power_zones.md` — Coggan 7-zone model, FTP definitions, zone training guidelines
  - `polarized_training.md` — Seiler 80/20 model, physiological basis, implementation
  - `sweet_spot_training.md` — 88–95% FTP training, classic workouts, SST vs polarized comparison
  - `periodization.md` — macrocycle/mesocycle/microcycle structure, annual planning, taper protocols
  - `recovery.md` — post-exercise nutrition, sleep science, HRV monitoring, overtraining prevention
- **Ingestion script** (`backend/scripts/ingest_cycling_science.py`):
  - Processes `backend/knowledge/*.md` seed files and queries the Semantic Scholar API (8 cycling-science topics, 10 papers each).
  - Chunks text ~500 tokens / 50-token overlap via tiktoken (character-based fallback when tiktoken is unavailable).
  - Embeds chunks using `text-embedding-3-small` via the existing `AsyncOpenAI` client.
  - Idempotent upsert on `(source_id, chunk_index)`; respects the 1 req/s unauthenticated S2 rate limit.
  - Uses `SEMANTIC_SCHOLAR_API_KEY` env var if set to raise the rate limit to 10 req/s.
- **New dependencies**: `pgvector>=0.3.0`, `tiktoken>=0.7.0`.
- **7 new pytest tests** in `backend/tests/test_rag.py` covering retrieval, graceful fallback, source formatting, and HTTP endpoint integration (175 total).

## [0.2.0] - 2026-04-18

### Changed

- `/ai/ask-trainer` — accepts only `{ question, contextWorkout? }`. Plan, profile, rider assessment, coach memory, and conversation history are now loaded from the database for the authenticated user
- `/ai/adapt-plan` — accepts only `{ recentFeedback }`. Plan and profile loaded from the database
- `/ai/generate-plan` — accepts an empty body. Profile and rider assessment loaded from the database
- `/ai/rate-workout` — accepts only `{ day }`. Profile loaded from the database
- `/ai/ask-trainer` now persists user and assistant chat messages via `crud.create_chat_message`
- `/ai/ask-trainer` now updates coach memory server-side via `ai_service.update_coach_memory` → `crud.upsert_coach_memory`
- `/ai/ask-trainer` now applies any `plan_updates` returned by the AI to the stored training plan via `crud.upsert_training_plan`
- `/ai/adapt-plan` now persists the updated plan via `crud.upsert_training_plan`
- `/ai/generate-plan` now persists the generated plan via `crud.upsert_training_plan`
- Added `_user_to_profile_dict(user)` helper in `routers/ai.py` to build camelCase profile dict from the `User` ORM model

### Removed

- `POST /ai/update-coach-memory` endpoint — memory updates are now handled atomically inside `/ai/ask-trainer`
- `UpdateCoachMemoryRequest` and `UpdateCoachMemoryResponse` schemas removed from `schemas.py`
- `ConversationMessageSchema` schema removed from `schemas.py` (no longer accepted from the client)

## [0.1.0] - 2026-04-14

### Added

- FastAPI application with async SQLAlchemy + PostgreSQL (asyncpg) and Alembic migrations
- User model with profile fields: bike type, training goal, race date, FTP, heart rate metrics, fitness level, AI provider preference
- JWT authentication: `/auth/login`, `/auth/register`, `/auth/session`; Authelia SSO support via `AUTHELIA_AUTH_ENABLED`
- Strava OAuth integration: connect/disconnect, activity fetching, stream data (watts, HR, cadence, velocity, time)
- Repository/data-access layer (`crud.py`) centralising all SQLAlchemy queries
- AI service (`services/ai_service.py`):
  - Training plan generation (`generate_training_plan`)
  - Training plan adaptation (`adapt_training_plan`)
  - AI coach chat with conversation history and coach memory (`ask_trainer`)
  - Coach memory updates (`update_coach_memory`)
  - Strava activity analysis with FTP estimation (`analyse_strava_activities`)
  - Post-workout rating (`rate_completed_workout`)
  - Support for OpenAI (`gpt-4o-mini`) and Google Gemini (`gemini-2.0-flash`)
- Ride analysis algorithms (`services/analysis.py`):
  - Ride purpose classification (recovery / endurance / tempo / VO2max intervals / sprints)
  - Interval detection from power streams
  - HR drift computation
  - Best N-minute power sliding window
  - HR-corrected FTP estimation (`_hr_corrected_ftp`)
  - HR zone computation
- Prompt construction module (`services/prompts.py`) for all AI system and user prompts
- Persistent coach memory and chat history stored in the database (`coach_memory`, `chat_messages` tables)
- Training plan and workout log persistence (`training_plans`, `workout_logs` tables)
- Rider assessment persistence with HR zones (`rider_assessments` table)
- `/ai/ask-trainer`: `contextWorkout` support for workout-scoped coaching; interval-aware plan updates; honest reflection on plan changes
- JWT secret validation at startup: server refuses to start with the insecure default in non-development environments
- Strava token auto-refresh before activity fetches
- Docker support with environment configuration
- Ansible-based Hetzner VPS deployment with Traefik reverse proxy and Let's Encrypt TLS
- Authelia SSO integration for production deployments
- 168 pytest tests with 82% coverage and Codecov integration
- GitHub Actions CI workflow (test → coverage upload)
