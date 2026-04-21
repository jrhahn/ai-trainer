# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.3] - 2026-04-21

### Changed

- **Token-usage compaction** (`services/ai_service.py`, `services/prompts.py`) — reduces the
  number of tokens sent per `ask-trainer` request to lower the risk of hitting per-minute TPM
  limits:
  - `MAX_CONVERSATION_HISTORY` reduced from 20 → **10** messages (saves ~1.5–3k tokens).
  - Upcoming plan context reduced from 14 → **7 days** ahead (saves ~700–2.8k tokens).
  - Plan entries sent to the model are now **slimmed** via `_slim_plan_entry()` — verbose fields
    (`description`, `intervals`, `keyFocusPoints`, `coachFeedback`) are stripped; only scheduling
    fields (`date`, `workoutType`, `durationMinutes`, `title`, `targetPower`, `completed`) are kept.
    This cuts per-entry token cost by ~50–70%.
  - Coach memory is **capped at 800 characters** (most recent notes), preventing unbounded growth
    from inflating the system prompt.

## [0.16.2] - 2026-04-21

### Changed

- **Gemini model upgrade** (`services/ai_service.py`) — switched `GEMINI_MODEL` from
  `gemini-2.0-flash` to `gemini-2.5-flash` (Gemini Flash 3). The 2.5 generation has higher
  per-minute token throughput limits on paid tiers, reducing the likelihood of 429
  RESOURCE_EXHAUSTED errors observed with 2.0-flash at low monthly spend.

## [0.16.1] - 2026-04-21

### Fixed

- **Gemini 429 RESOURCE_EXHAUSTED** (`services/ai_service.py`, `routers/ai.py`) — when the Gemini
  API returns an HTTP 429 rate-limit error the backend previously let the raw exception propagate,
  producing an opaque `500 Internal Server Error`. The fix adds a dedicated `AIRateLimitError`
  exception: both `_chat()` and `_chat_history()` now catch `google.genai.errors.ClientError` with
  `code == 429` and raise `AIRateLimitError` instead. All AI endpoints (`analyse-activities`,
  `generate-plan`, `adapt-plan`, `ask-trainer`, `rate-workout`, `refresh-login-summary`) convert
  this into an HTTP **503 Service Unavailable** with a user-friendly message. The
  `update_coach_memory` call inside `ask-trainer` is treated as best-effort: a rate-limit there is
  logged at `INFO` level and does not block the response.

  The Gemini free-tier 429 is a **per-minute / per-day token-quota** error rather than a billing
  limit. Common triggers even at low spend: very long conversation histories, large system prompts
  (training plan + RAG context + coach notes all included in every request), or Gemini's own
  per-region capacity limits. The 503 response lets the frontend show a clear retry message instead
  of a generic server crash.

### Tests

- `test_chat_raises_ai_rate_limit_error_on_gemini_429` — `_chat` raises `AIRateLimitError` on a
  mocked 429 `ClientError`.
- `test_chat_history_raises_ai_rate_limit_error_on_gemini_429` — same for `_chat_history`.
- `test_ask_trainer_endpoint_returns_503_on_rate_limit` — `/ask-trainer` returns 503.
- `test_analyse_activities_endpoint_returns_503_on_rate_limit` — `/analyse-activities` returns 503.
- `test_generate_plan_endpoint_returns_503_on_rate_limit` — `/generate-plan` returns 503.

## [0.16.0] - 2026-04-20

### Changed

- **Coach talking style** (`services/prompts.py`) — `COACH_PERSONA` and `RUNNING_COACH_PERSONA`
  are rewritten to give the AI a warm, empathic, friend-like voice:
  - The coach is framed as *"a great friend who happens to know a lot about [sport]"* — casual and
    approachable, never stiff or clinical.
  - The AI is instructed to use the athlete's first name, celebrate wins enthusiastically,
    acknowledge struggles with compassion, and never make the athlete feel judged for missing a
    session or falling short.
  - Honest feedback is preserved but framed with kindness: *"like a great friend who tells you the
    truth because they care about you."*
  - A shared `_FRIEND_COACH_TRAITS` template eliminates duplication between the cycling and running
    personas.

### Tests

- New section `COACH_PERSONA / RUNNING_COACH_PERSONA — friendly talking style` in
  `tests/test_ai_service_unit.py` (10 assertions across 9 test functions):
  - `test_coach_persona_is_friend_framed` — verifies friend framing and sport specificity
  - `test_running_coach_persona_is_friend_framed` — mirrors the above for running
  - `test_personas_are_sport_distinct` — asserts the two personas are unique and non-overlapping
  - `test_coach_persona_empathy_traits` — checks for compassion/no-judgement language
  - `test_coach_persona_encourages_direct_address` — verifies "address the athlete directly"
  - `test_coach_persona_retains_long_term_philosophy` — guards against losing the development ethos
  - `test_analyse_activities_system_uses_correct_persona` — ensures routing to the right persona by sport type
  - `test_generate_plan_system_uses_coach_persona` — checks persona is embedded in plan generation
  - `test_rate_workout_system_uses_coach_persona` — checks persona is embedded in workout rating
  - `test_friend_coach_traits_template_interpolation` — validates `{sport}` placeholder is always resolved



### Added

- **Post-login ride summary (`loginSummary`)** — the `analyse-activities` endpoint now generates a structured 4-part coach summary covering: (1) what the athlete did and what was strong/improvable, (2) FTP and fitness insights, (3) training plan alignment (how recent rides matched the plan), and (4) actionable conclusions for upcoming sessions. Stored in the new `rider_assessments.login_summary` column.

- **Training plan context in activity analysis** — `analyse_strava_activities` now accepts an optional `training_plan` parameter. When a plan exists the prompt receives it so the AI can compare actual rides against planned sessions and produce an accurate plan-alignment section in `loginSummary`.

- **`loginSummary` field added to `RiderAssessmentSchema`** (`schemas.py`) — the field is serialised as `loginSummary` (camelCase) for the frontend.

- **`login_summary` column added to `rider_assessments`** (`models.py`) — nullable `Text` column; Alembic migration `20260420_000001`.

### Changed

- **`analyse_activities_user` prompt helper** (`services/prompts.py`) — accepts optional `training_plan` list; when provided, appends the plan JSON to the user message so the AI can assess alignment for `loginSummary`.

- **`analyse_activities` router** (`routers/ai.py`) — fetches the training plan once and reuses it for both the AI call (plan-alignment context) and the training-load computation (no extra DB round-trip).

## [0.14.0] - 2026-04-20

### Added

- **`analyse_fit_activity` AI function** (`services/ai_service.py`) — lightweight analysis path for `.fit` imports where only summary metrics are available (no per-second streams):
  - Cycling: FTP estimated as `avg_power × AVG_POWER_TO_FTP_RATIO`
  - Running: `estimatedFTP` forced to `null`; threshold HR estimated via `max_heart_rate × LTHR_RATIO` when max HR is provided
  - Returns the same shape as `analyse_strava_activities` so `upsert_rider_assessment` can consume it unchanged

- **`.fit` uploads now trigger AI analysis and write a metric snapshot** (`routers/users.py`):
  - After saving the `WorkoutLog` row, calls `analyse_fit_activity` (best-effort — the upload response is never blocked on AI errors)
  - AI result is persisted to `rider_assessment` via `crud.upsert_rider_assessment`, giving non-Strava users last-ride feedback and ride insights on their dashboard
  - `crud.create_athlete_metric_snapshot` is called with `source="fit_upload"` so the athlete's FTP/HR history chart is populated; falls back to algorithmic estimates when AI is unavailable

- **Sport-type-aware AI prompts** (`services/prompts.py`):
  - `RUNNING_COACH_PERSONA` constant — mirrors `COACH_PERSONA` but with a running background
  - `analyse_activities_system(sport_type)` branches on `sport_type`: running activities receive HR-zone categories, running vocabulary (run/runs), and no references to watts or FTP; cycling retains the previous prompt unchanged
  - `analyse_activities_user(sport_type)` adjusts the instruction note — for running, the AI is explicitly told to set `estimatedFTP` to `null` and use pre-computed threshold HR verbatim

- **`analyse_strava_activities` now accepts `sport_type`** (`services/ai_service.py`) — power-based FTP computation and per-ride interval analysis are skipped when `sport_type` is `"running"` or `"run"`, preventing nonsensical cycling analysis for runners

### Tests

- `analyse_fit_activity` mock added to `conftest.py` `mock_ai_service` fixture
- New contract test `test_fit_upload_writes_metric_snapshot` — uploads a mocked `.fit` file and asserts that an `AthleteMetricSnapshot` row with `source="fit_upload"` appears in `GET /users/me/metrics-history`



### Changed

- **`adapt_plan_system` prompt** — the instruction `"Keep the same date fields"` is replaced with explicit guidance: future days keep their dates; past incomplete days must be rescheduled to upcoming dates starting from today.
- **`adapt_plan_user` prompt** — when incomplete days with past dates are present, a `NOTE:` sentence is appended that tells the AI coach exactly how many sessions are overdue and asks it to reschedule them to current/upcoming dates.

## [0.12.0] - 2026-04-19

### Added

- **`.fit` file upload — multi-sport / non-Strava ingest** (`routers/users.py`, `schemas.py`, `crud.py`, `models.py`):
  - New `POST /users/me/upload-fit` endpoint — accepts a `.fit` file (Garmin / Wahoo / Zwift export), parses it with `fitparse`, normalises session/record messages into a `WorkoutLog` row, and returns summary metrics (`sport_type`, `duration_minutes`, `average_power`, `average_heart_rate`)
  - `sport_type: str` column added to `WorkoutLog` (default `"cycling"`) — populated from the FIT `session.sport` field; enables multi-sport filtering in future
  - `FitUploadResponse` schema added to `schemas.py`
  - Alembic migration `20260419_000001` — adds `sport_type` column to `workout_logs`
  - Dependencies added: `fitparse>=1.2.0`, `python-multipart>=0.0.22`

### Tests

- 4 new backend contract tests covering:
  - `GET /users/me/metrics-history` empty list for new user
  - `GET /users/me/metrics-history` snapshot populated after `analyse-activities`
  - `POST /users/me/upload-fit` rejects non-.fit files with HTTP 422
  - `POST /users/me/upload-fit` rejects corrupt .fit data with HTTP 422

## [0.11.0] - 2026-04-19

### Added

- **Athlete Long-Term Memory / Progression Model** (`models.py`, `crud.py`, `routers/users.py`, `schemas.py`, `routers/ai.py`):
  - New `athlete_metric_snapshots` table — stores per-user time-series snapshots of `ftp`, `threshold_hr`, `ctl`, `atl`, `tsb`, `source`, and `recorded_at`; Alembic migration `20260418_000002`
  - CRUD helpers: `create_athlete_metric_snapshot`, `get_athlete_metric_history(db, user_id, limit=90)` returning snapshots oldest-first
  - `AthleteMetricSnapshotSchema` (camelCase via `CamelModel`) and `MetricsHistoryResponse` schemas
  - `POST /ai/analyse-activities` now records a metric snapshot after every Strava analysis; CTL/ATL/TSB are computed from the current training plan via `compute_training_load` and only stored when a valid FTP is available
  - `GET /users/me/metrics-history` — new authenticated endpoint returning the athlete's complete metric snapshot history

### Tests

- 8 new backend tests covering:
  - `create_athlete_metric_snapshot` and `get_athlete_metric_history` (null values, ascending order, limit)
  - `GET /users/me/metrics-history` (empty list, post-analysis snapshot, auth guard)

## [0.10.0] - 2026-04-19

### Added

- **Race-Day Readiness Score** (`services/analysis.py`, `routers/ai.py`, `schemas.py`):
  - `compute_readiness_score(ctl, atl, tsb, days_until_race) -> dict` — pure Python function producing a 0–100 readiness score as a weighted blend of form (65 %, TSB-based, peaks at TSB +10) and fitness (35 %, CTL-based, capped at 100)
  - `_project_training_load(plan_days, ftp, target_date_str) -> dict` — forward-projects CTL/ATL/TSB to any future date by evaluating only plan days on or before `target_date_str`; enables race-day projections
  - `ReadinessScoreResponse` schema — includes `score`, `form_score`, `fitness_score`, `ctl`, `atl`, `tsb`, `days_until_race`, `race_date`, and optional `projected_score/ctl/atl/tsb` at race day
  - `GET /ai/readiness-score` — authenticated endpoint that computes the current readiness score from plan days up to today, derives `days_until_race` from the user's `race_date`, and (when race is in the future and FTP is known) appends a forward-projected score at race day

- **Auto-taper injection** (`services/ai_service.py`, `services/prompts.py`):
  - `adapt_training_plan()` now detects `0 ≤ days_until_race ≤ 14` and passes a `taper_days_remaining` value to `adapt_plan_user()`
  - `adapt_plan_user()` gains an optional `taper_days_remaining: int | None` parameter; when set it appends an explicit `⚠️ TAPER ALERT` block instructing the LLM to cut volume ~40%, retain intensity, and target TSB +5 to +15 — enforcing taper structurally rather than relying on a passive prompt hint

### Tests

- 6 new unit tests in `backend/tests/test_ai_service_unit.py` covering `compute_readiness_score`:
  - Expected return keys
  - Score/component scores always in 0–100 range across a range of TSB values
  - Peak form (TSB = +10, high CTL → `form_score = 100`, `score > 80`)
  - Severe fatigue (TSB ≤ −30 → `form_score = 0`)
  - Zero load (CTL = ATL = TSB = 0 → `form_score = 50`, `fitness_score = 0`, `score = 32.5`)
  - `days_until_race` preserved in return value

## [0.9.0] - 2026-04-19

### Added

- **Expanded RAG knowledge base** — three new structured seed files added to `backend/knowledge/`:
  - `critical_power.md` — Monod-Scherrer two-parameter critical-power (CP) model, W′ (anaerobic work capacity), power-duration curve equation, field-test protocols (3-min all-out, multiple time-trials), W′ balance reconstitution modelling (Skiba et al., 2012), CP vs FTP distinction, and training implications for raising CP and expanding W′
  - `heat_altitude_adaptation.md` — acute heat-stress physiology, 10–14 day heat acclimatisation protocol with adaptation timeline (plasma volume, sweat rate, core temperature, HR), pre-cooling strategies (ice vest, ice slurry, cold-water immersion), hydration targets; altitude performance decrements by elevation (1500–4000 m), LHTH/LHTL/IHE strategies, practical altitude camp planning, iron status guidance, AMS prevention
  - `nutrition_timing.md` — carbohydrate loading (8–12 g/kg/day × 3 days), pre-race meal windows (3–4 h, 1–2 h, 15–30 min), on-bike intake by duration (0–120 g/h), multiple-transporter carbohydrates (2:1 glucose:fructose, gut training), post-exercise glycogen resynthesis window, MPS protein dosing (20–40 g), bedtime casein, caffeine ergogenic evidence (3–6 mg/kg), stage-race daily CHO targets
- **9 new Semantic Scholar search queries** in `backend/scripts/ingest_cycling_science.py` — covers critical power/W′, heat acclimatisation, altitude training, and nutrition timing; total queries raised from 8 to 17

### Changed

- `docs/update_rag.md` — updated to enumerate all 8 seed files with topic summaries and list all 17 Semantic Scholar search queries

## [0.8.0] - 2026-04-19

### Changed

- **AI service layer clean-up** — resolved several code-quality issues:
  - Stripped `_` prefix from all exported symbols in `analysis.py` (11 symbols, e.g. `_best_n_min_power` → `best_n_min_power`, `_AVG_POWER_TO_FTP_RATIO` → `AVG_POWER_TO_FTP_RATIO`)
  - Moved inline `import math` to module level in `analysis.py`; removed unused `total_time` variable in `classify_ride_purpose`
  - Extracted the FTP-estimation loop (~45 lines) from `ai_service.py` into a new `compute_ftp_from_streams(streams_by_id, max_heart_rate) → (ftp, threshold_hr)` function in `analysis.py`
  - Added `analyse_activities_computed_section()` to `prompts.py` — prompt text that was previously built inline in `ai_service.py`
  - Replaced three `__import__("datetime").datetime.now().date().isoformat()` calls with a top-level `import datetime` and `datetime.date.today().isoformat()`
  - Eliminated `_openai_chat`, `_openai_chat_history`, `_gemini_chat`, `_gemini_chat_history` — provider dispatch inlined directly into `_chat` / `_chat_history`
  - `classify_question` now accepts `provider: str = "openai"` and routes through `_chat(provider, ...)` instead of hardcoding OpenAI
  - `routers/ai.py` passes `provider=_provider(current_user)` to `classify_question` so Gemini callers are no longer silently routed to OpenAI for classification

### Added

- **Workout execution feedback loop with Strava stream analysis** (`services/analysis.py`, `schemas.py`, `routers/ai.py`, `services/ai_service.py`, `services/prompts.py`) — `rate_completed_workout` now optionally fetches per-second Strava stream data and computes an objective planned-vs-actual delta before calling the AI coach:
  - `RateWorkoutRequest` gains an optional `strava_activity_id: int | None` field; when provided the router fetches the activity's streams (watts/HR/cadence/time) from Strava, computes the delta, and forwards it to the prompt. Failures are caught and logged as warnings so the rating always completes.
  - Four new pure-Python functions in `analysis.py`:
    - `_normalized_power()` — standard 30-second rolling-average NP
    - `_time_in_power_zones()` — seconds spent in each of the 7 standard power zones (Z1 < 55 % FTP … Z7 > 150 % FTP)
    - `_detect_intensity_spikes()` — identifies non-overlapping 15-minute windows where average power exceeded the planned target midpoint by more than 10 %
    - `compare_planned_vs_actual(planned, streams, ftp)` — orchestrates the above to produce a structured delta dict: avg/NP power vs target (absolute watts + percentage), time-in-zones, HR drift (linear-regression slope across the session), HR vs target, and a list of intensity spikes
  - `rate_completed_workout()` in `ai_service.py` gains a `stream_delta: dict | None` keyword argument and forwards it to `rate_workout_user()`
  - `rate_workout_system()` instructs the AI to use stream data for precise, actionable language (e.g. *"you went 15 % over Z2 intensity in the first 30 min, which erodes your aerobic base and costs recovery"*)
  - `rate_workout_user()` renders a structured *"Objective stream data (from Strava)"* section in the prompt when `stream_delta` is present, including avg/NP delta lines, per-zone time breakdown, HR drift direction, and per-spike annotations
- **Frontend wiring** (`frontend/src/services/ai.ts`, `frontend/src/pages/WorkoutPage.tsx`) — `rateCompletedWorkout()` accepts an optional `stravaActivityId`; `WorkoutPage` reads the matching Strava activity from the React Query cache (keyed on `start_date` date prefix) and passes its ID automatically

## [0.6.0] - 2026-04-18

### Security

- **JWT secret startup guard** (`auth.py`, `main.py`) — the server now refuses to start when `JWT_SECRET` is still set to the insecure default `"change-me-in-production"` and `APP_ENV` is not a development/test environment (`development`, `dev`, `local`, `test`, `testing`). A `RuntimeError` with a clear message is raised inside the FastAPI `lifespan` handler so misconfigured production deployments fail loudly at boot rather than silently accepting forgeable tokens. Five new tests in `backend/tests/test_auth.py` cover all cases (raises in `production`/`staging` with default; passes in `development`/`test` with default; passes in `production` with a custom secret).

## [0.5.0] - 2026-04-18

### Added

- **`workoutPurpose` and `keyFocusPoints` fields** — `TrainingDaySchema` and `PlanDayUpdateSchema` gain two new optional fields:
  - `workout_purpose: Optional[str]` — 1–2 sentences describing the physiological goal of the session and why it is placed at this point in the plan
  - `key_focus_points: Optional[list[str]]` — 3–5 action-verb coaching cues for the athlete to focus on during execution

### Changed

- **`generate_plan_system()`** (`services/prompts.py`) — now instructs the AI to populate `workoutPurpose` and `keyFocusPoints` on every plan day; strengthens the `description` instruction to state **exact** power/HR targets derived from the athlete's FTP and threshold HR (percentages must always be translated to absolute watts/bpm)
- **`adapt_plan_system()`** (`services/prompts.py`) — same requirements applied to adapted days: `workoutPurpose`, `keyFocusPoints`, and number-grounded `description` are mandatory in every returned day
- **`ask_trainer_plan_updates_rule()`** (`services/prompts.py`) — any `planUpdates` entry emitted by the AI coach must now include `workoutPurpose`, `keyFocusPoints`, and a description with exact targets; the field list in both the context-workout and general rule branches is updated accordingly

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
