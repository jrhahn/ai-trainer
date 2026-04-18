# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
