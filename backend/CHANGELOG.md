# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-04-18

### Added

- **CTL/ATL/TSB training load metrics** (`services/analysis.py`) — new `_compute_training_load(plan_days, ftp) -> dict` function that computes:
  - Daily TSS approximated from `durationMinutes` + `targetPower` mid-point (or a workout-type heuristic when no power target is given)
  - CTL (chronic training load) — 42-day exponential weighted average of daily TSS
  - ATL (acute training load) — 7-day exponential weighted average of daily TSS
  - TSB (training stress balance / form) — CTL − ATL
  - Returns `{"ctl": float, "atl": float, "tsb": float, "daily_tss": list[float]}`
- **Training load injection into AI prompts** (`services/prompts.py`):
  - `ask_trainer_system` now includes a `training_load_section` with CTL/ATL/TSB and coaching guidance (*TSB < −20 → prioritise recovery; TSB > +10 before a key workout → increase intensity*)
  - `adapt_plan_user` accepts a `training_load` kwarg and surfaces the same metrics alongside rider assessment
- **Chain-of-thought `"thinking"` field** (`services/prompts.py`, `services/ai_service.py`) — `ask_trainer_system` instructs the model to reason through four steps (intent, fatigue state, training-principles conflict, best answer) and place the reasoning in a `"thinking"` field; `ask_trainer()` pops this key before returning so it never reaches the frontend
- **Training plan principles in `adapt_plan_system`** (`services/prompts.py`) — `adapt_plan_system()` now embeds `TRAINING_PLAN_PRINCIPLES` (the same no-back-to-back-hard-days / weekday-duration-cap block already used in `generate_plan_system()`), preventing adapted plans from violating core periodisation rules
- **`rider_assessment` forwarded to plan adaptation** (`services/ai_service.py`, `routers/ai.py`) — `adapt_training_plan()` and `adapt_plan_user()` now accept a `rider_assessment` dict so the AI knows the athlete's FTP and rider type when adapting; the `/ai/adapt-plan` router loads and passes the assessment automatically
- **Structured `rate_completed_workout` response** (`services/prompts.py`, `services/ai_service.py`, `schemas.py`) — `rate_workout_system` now returns JSON `{"feedback": str, "flag_for_adaptation": bool}`; `rate_completed_workout()` returns `dict` instead of `str`; `RateWorkoutResponse` schema gains `flag_for_adaptation: bool`
  - `flag_for_adaptation` is `true` when perceived effort ≫ planned intensity, actual duration is significantly shorter than planned, or athlete notes indicate fatigue/illness/pain
- **Auto-adaptation on flagged workouts** (`routers/ai.py`) — `/ai/rate-workout` automatically calls `adapt_training_plan` when `flag_for_adaptation` is `True`, using the completed workout's feedback as context; failures are caught and logged as warnings so the rating response is always returned
- **Two-step classify → respond in `ask_trainer`** (`services/prompts.py`, `services/ai_service.py`, `routers/ai.py`):
  - New `ask_trainer_classify_system()` / `ask_trainer_classify_user(question)` prompts classify the question into one of five categories and set `needs_science_rag: bool`
  - New `classify_question(question) -> dict` function calls `gpt-4o-mini` (fast/cheap) and returns `{"category": ..., "needs_science_rag": bool}`; returns a safe default on failure with a warning log
  - `/ai/ask-trainer` router calls `classify_question` first and only invokes `retrieve_cycling_context` (pgvector embedding lookup) when `needs_science_rag` is `True`, skipping the RAG overhead for simple plan/schedule queries
  - Classification result is forwarded into the full system prompt so the AI knows what type of answer is expected

### Changed

- `adapt_plan_system()` now includes `TRAINING_PLAN_PRINCIPLES` (previously only `generate_plan_system` did)
- `adapt_plan_user()` signature extended with optional `rider_assessment` and `training_load` kwargs (backward-compatible)
- `ask_trainer_system()` signature extended with optional `training_load` and `classification` kwargs (backward-compatible)
- `ask_trainer()` signature extended with optional `classification` kwarg (backward-compatible)
- `/ai/ask-trainer` router no longer calls `retrieve_cycling_context` unconditionally; RAG is gated behind the classification result
- `/ai/rate-workout` router now depends on `db` (needed for auto-adaptation plan persistence)

### Tests

- `backend/tests/test_ai_service_unit.py` — 15 new tests:
  - `_compute_training_load`: empty input, zero FTP, expected keys and values, `targetPower` mid-point TSS, TSB precision
  - `ask_trainer`: asserts `"thinking"` is never in the return value; asserts RAG is skipped when `needs_science_rag=False`; asserts CTL/ATL/TSB appear in the system prompt when FTP is known
  - `rate_completed_workout`: structured dict return, `flag_for_adaptation=True` for hard/short sessions, `flag_for_adaptation=False` for normal sessions
  - `adapt_plan_system`: includes `TRAINING_PLAN_PRINCIPLES` and TSB guidance text
  - `classify_question`: returns safe default on failure; parses response correctly
- `backend/tests/test_ai.py` — 3 new integration tests:
  - `test_rate_workout_returns_flag_for_adaptation_false` — normal session returns `flag_for_adaptation=False`
  - `test_rate_workout_flag_triggers_adapt_plan` — flagged session calls `adapt_training_plan`
  - `test_ask_trainer_classify_called_and_rag_skipped_when_not_needed` — `classify_question` is called on every ask-trainer request
- `backend/tests/test_rag.py` — 2 existing tests updated to set `needs_science_rag=True` via the `classify_question` mock when testing RAG source forwarding (previously RAG was called unconditionally)
- 211 tests total (up from 168)



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
- **CI integration job** — a new `integration` GitHub Actions job spawns the backend with a throwaway SQLite database and runs the 24-test TypeScript integration suite against it, validating the full HTTP contract between the frontend service layer and the FastAPI application.

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
