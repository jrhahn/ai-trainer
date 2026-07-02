# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.39.5] - 2026-07-02

### Fixed

- **Transient Strava stream failures no longer imported as empty-stream activities**
  (`services/strava_service.py`, `services/activity_sync.py`, `routers/strava.py`) —
  `fetch_activity_streams` returned `{}` for both "activity genuinely has no
  streams" and a transient 429/5xx/timeout, so a blip during stream download
  permanently imported an activity with no power/HR data, silently corrupting
  TSS/CTL/ATL/TSB with no retry (#325). A new `fetch_activity_streams_strict`
  raises `StravaStreamUnavailable` on transient failures (the existing lenient
  wrapper still returns `{}` for enrichment paths). Background sync now skips such
  an activity and holds the cursor below it so it is retried next tick instead of
  being lost; bulk history import skips it (counted in `skipped`) rather than
  baking in degraded data. A genuine no-streams response (2xx-empty / permanent
  4xx) is still imported summary-only as before.

## [0.39.4] - 2026-07-02

### Fixed

- **Race-context sync no longer risks clobbering a concurrent coach-memory edit**
  (`routers/users.py`, `crud.py`) — `_sync_race_context` (run on race-event
  create/update/delete) did an unlocked read-modify-write of the coach memory to
  refresh its race section, so a `PUT /coach-memory` landing in the same instant
  could be lost. The read now locks the row (`crud.get_coach_memory(...,
  for_update=True)`, `SELECT … FOR UPDATE`), serialising the merge against
  concurrent writers. Closes the last instance of the stale-snapshot clobber
  class (#342/#345/#346).

## [0.39.3] - 2026-07-01

### Fixed

- **Coach-memory background update no longer clobbers concurrent edits**
  (`routers/ai.py`, `crud.py`) — after a chat, the background memory update based
  its rewrite on the memory snapshot captured at request time and blind-overwrote
  the row, so an edit the athlete made via `PUT /coach-memory` while the model was
  generating was silently lost (#346). The task now re-reads the current memory as
  its base and writes with a row-locked compare-and-set
  (`crud.update_coach_memory_if_unchanged`), retrying against the fresh value on a
  detected concurrent edit. New helper is atomic (`SELECT … FOR UPDATE`), so a
  committed edit from another transaction is never overwritten.

## [0.39.2] - 2026-07-01

### Tests

- **Cover the plan-day history data layer** (`tests/test_crud.py`) — direct tests
  for `crud.record_plan_day_changes` (the empty-changes no-op guard, row
  insertion, and the `applied` default) and `crud.list_plan_day_history`'s date
  filter (#343).

## [0.39.1] - 2026-07-01

### Added

- **Admin endpoint to inspect plan-day history** (`routers/admin.py`) — new
  `GET /admin/users/{user_id}/plan-history` (admin-authenticated, optional
  `?date=` filter and `limit`) returns a user's per-day plan change log newest
  first, each entry showing the day before/after, the trigger, and whether it was
  applied or blocked. Backs debugging of plan changes (e.g. "why did today revert
  after a ride?") and a future analytics surface. Reuses
  `crud.list_plan_day_history` (#343).

## [0.39.0] - 2026-07-01

### Added

- **Per-day training-plan change history** (`models.py`, `crud.py`,
  `services/plan_pipeline.py`, Alembic `20260701_000001`) — a new append-only
  `plan_day_history` table records one row per changed plan day per write,
  capturing the day before and after and which trigger caused it (manual save,
  coach chat, ride review, nightly maintenance, …). The live plan stays in
  `TrainingPlan.plan` and the UI still reads only the latest version; this log is
  for analytics and learning athlete behaviour (#343). Automated changes blocked
  by a user pin or a completed day (#342/#345) are logged with `applied=False` as
  an "attempted correction" signal, and the recorded source is the real trigger
  so manual and coach-chat edits stay distinguishable. New `crud` helpers
  `record_plan_day_changes` / `list_plan_day_history` back future analytics
  surfaces.

## [0.38.11] - 2026-07-01

### Fixed

- **Manual plan edits no longer silently reverted by automated triggers**
  (`services/plan_pipeline.py`, `schemas.py`, `routers/ai.py`, `routers/users.py`,
  `services/plan_maintenance.py`, `services/ride_matching.py`) — background
  triggers (post-ride review, nightly maintenance, workout-rating auto-adapt)
  could overwrite a day the athlete had set manually, because the pipeline's
  concurrent-edit guard only detected edits made in the read→write window; an
  edit persisted before the trigger read the plan was baked into its baseline and
  treated as fair game (#342). Each day now carries a persistent `source` marker:
  user-authored triggers (manual save, coach chat) pin changed days as `"user"`,
  and background triggers may not overwrite a pinned, not-yet-completed day.
  User-requested replans (generate, adapt, next-ride) stay authoritative. The
  `source` is threaded from all eight trigger call sites via a single
  `PLAN_SOURCES` registry, and only content-changed days are re-stamped so no-op
  writes cause no plan churn or summary invalidation.
- **Completed workout days protected on the full-plan path**
  (`services/plan_pipeline.py`) — the per-day update path already skipped
  completed days, but the full-plan path never consulted the `completed` marker,
  so a background full-plan write could have the model rewrite a completed day or
  drop it entirely when the proposal omitted it (#345). Background triggers now
  restore every currently-completed day unchanged and never drop it; user edits
  still bypass the guard so they can set and correct completed days and feedback.

## [0.38.10] - 2026-06-22

### Added

- **Import historical coach conversations** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `schemas.py`) — new
  `POST /ai/extract-athlete-facts` runs an extraction prompt over a pasted
  conversation transcript and returns candidate durable athlete traits
  (fact, category, confidence, source snippet) **without persisting them**.
  The athlete reviews candidates in Settings and accepts chosen ones via the
  existing `POST /users/me/athlete-memory-facts`, after which they feed coach
  recommendations. Candidates are deduplicated, confidence-capped at 0.9, and
  the transcript is length-bounded.

## [0.38.9] - 2026-06-22

### Added

- **Transparent physiology vs personal-context rationale** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `schemas.py`) — the coach now reasons
  in two explicit layers and returns separate `physiologyRationale` and
  `contextRationale` fields from `/ai/ask-trainer`. When the layers diverge, the
  natural response says so ("the numbers say X, but knowing you I'd do Y"), so the
  athlete can see when a call is about them rather than the workout file. The chat
  surfaces this as a compact, collapsible "Why this advice?" disclosure.

## [0.38.8] - 2026-06-22

### Added

- **Delete learned athlete traits** (`crud.py`, `routers/users.py`) — new
  `DELETE /users/me/athlete-memory-facts/{fact_id}` endpoint permanently removes
  a learned trait, scoped to the owning user (404 when missing). This backs the
  new Settings UI for reviewing, correcting, confirming, and deleting the
  personal traits the coach has learned, so coaching memory stays inspectable
  and correctable.

## [0.38.7] - 2026-06-22

### Fixed

- **Empty AI coach responses** (`services/ai_service.py`, `routers/ai.py`) —
  `/ai/ask-trainer` now retries blank LLM `response` payloads (2 retries with
  0.5s→1s exponential backoff) and rejects them with a 502 before persisting
  chat messages, so transient malformed provider replies recover automatically
  or surface as retryable request failures instead of empty assistant bubbles.

## [0.38.6] - 2026-06-21

### Fixed

- **Contained duplicate ride imports** (`services/activity_identity.py`,
  `crud.py`) — same-day cycling imports are now collapsed when a shorter
  activity is substantially contained within a longer imported activity, while
  preserving genuinely separate rides with different start windows.

## [0.38.5] - 2026-06-21

### Fixed

- **Ride label explanations** (`services/prompts.py`) — recent activity context
  now includes ride duration and displayed match labels, and label explanations
  explicitly avoid inventing missing-data causes unless activity context supports
  that claim.

## [0.38.4] - 2026-06-21

### Fixed

- **Multi-ride plan matching** (`services/ride_matching.py`) — same-day
  duration-focused cycling plans now consider the combined duration of all
  rides, auto-match the closest ride when the day does not collectively satisfy
  the plan, and label extra rides as `Additional` or `Too much` based on
  intensity/TSS evidence.

## [0.38.3] - 2026-06-20

### Fixed

- **Duplicate activity analysis canonicalization** (`services/activity_identity.py`,
  `crud.py`, `services/activity_imports.py`, `routers/ai.py`) — same-day ride
  imports with matching names, nearby start times, and small duration drift now
  collapse to one canonical ride metric, stale duplicate history rows are
  hidden, and near-identical activities are deduplicated before activity
  analysis calls without merging separate same-name rides that start at
  different times.

## [0.38.2] - 2026-06-19

### Fixed

- **Repeat hard-session guard** (`services/prompts.py`) — Ask Trainer,
  plan adaptation, and next-session recommendations now treat actual recent
  hard sessions as authoritative before preserving or creating VO2max/HIIT
  work, so a planned repeat high-intensity day within roughly 48 hours must be
  moved or replaced unless an explicit exception is justified.

## [0.38.1] - 2026-06-19

### Fixed

- **Rest-day coach justification** (`services/prompts.py`) — Ask Trainer now
  evaluates challenged rest-day recommendations against CTL/ATL/TSB, recent
  TSS, RPE, subjective freshness, and availability constraints, so a second
  complete rest day must be justified by concrete fatigue evidence and corrected
  workout timing is re-evaluated before recommending easy Z2 or full rest.

## [0.38.0] - 2026-06-18

### Added

- **Persistent ride match feedback** (`schemas.py`, `routers/users.py`) —
  ride-feedback submissions can now include an athlete plan-match correction,
  which is stored in `ride_metrics.label_override` and returned with the
  updated ride so the frontend can persistently correct badges after feedback.
- **Expiring athlete availability constraints** (`models.py`, `crud.py`,
  `services/availability.py`, `routers/ai.py`) — chat messages such as
  "Friday I have no time for training" are now captured as structured
  `athlete_availability_constraints` rows, included in coach prompts while
  active, and automatically ignored after their expiry date.

### Changed

- **Coach plan updates preserve hard availability constraints**
  (`services/prompts.py`) — Ask Trainer, plan adaptation, and next-ride
  recommendation prompts now treat user availability constraints from coach
  memory, athlete context, or the current conversation as binding, so workouts
  are not moved onto unavailable days even when that placement would be
  physiologically optimal.
- **Server-side guard for unavailable days** (`routers/ai.py`) — generated
  plans, adapted plans, Ask Trainer plan updates, and next-ride recommendation
  updates are sanitized before persistence so non-rest workouts cannot be saved
  on active no-training constraint dates.

## [0.37.0] - 2026-06-18

### Changed

- **Targeted clarification for ambiguous recommendations**
  (`services/prompts.py`) — Ask Trainer and next-session recommendation prompts
  can now ask exactly one short learning question when physiology and athlete
  context leave materially different recommendations plausible, while avoiding
  extra questions when recent data, known context, or fatigue/safety signals
  already make the recommendation clear. Answers to recommendation
  clarification questions are treated as potentially durable coach-memory
  context.

## [0.36.0] - 2026-06-18

### Changed

- **Psychological training tendencies in coach memory** (`services/prompts.py`)
  — coach-memory extraction now has an explicit category for durable patterns
  such as overtraining or undertraining bias, rest anxiety, FOMO, overanalysis,
  reassurance seeking, needing permission to rest, doing too much when fresh,
  and psychological benefit from MTB or easy/social rides, while avoiding
  one-off moods as permanent traits unless repeated, user-confirmed, or
  actionable for recovery, intensity, or rest decisions.

## [0.35.0] - 2026-06-18

### Changed

- **Two-layer coach recommendations** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `services/ride_matching.py`) —
  Ask Trainer and next-session recommendation prompts now explicitly separate
  physiology inputs (CTL/ATL/TSB, recent load, feedback, planned stimulus) from
  athlete-context inputs (motivation, rest tolerance, adherence pattern,
  structured athlete context, and evidence-backed memory facts) so personal
  context can decide between physiologically similar options.

## [0.34.0] - 2026-06-18

### Added

- **Evidence-backed athlete memory facts** (`models.py`, `crud.py`,
  `routers/users.py`, `schemas.py`,
  `alembic/versions/20260618_000002_add_athlete_memory_facts.py`) — adds
  durable per-user coaching facts with category, source snippet/exchange,
  first-observed and last-confirmed timestamps, confidence, observation count,
  and active/stale/rejected/user-confirmed status, exposed via
  `GET/POST/PATCH /users/me/athlete-memory-facts`.
- **Prompt-safe durable memory filtering** (`crud.py`, `routers/ai.py`,
  `services/ai_service.py`, `services/prompts.py`) — Ask Trainer now receives
  only user-confirmed or sufficiently fresh, high-confidence facts, while stale,
  rejected, or low-confidence observations are omitted from coach prompts.

## [0.33.0] - 2026-06-18

### Added

- **Structured athlete context model** (`models.py`, `crud.py`, `routers/users.py`,
  `schemas.py`, `alembic/versions/20260618_000001_add_athlete_context.py`) —
  adds a durable per-user athlete context with training tendency, rest response,
  motivation drivers, adherence pattern, strengths, weaknesses, preferred
  terrain/session types, coaching risks, and notes, exposed via
  `GET/PUT /users/me/athlete-context`.
- **Athlete context in coach prompts** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — Ask Trainer now receives a
  compact structured athlete-context section alongside existing free-text coach
  memory so durable coaching traits can guide replies without breaking coach
  memory behavior.

## [0.32.6] - 2026-06-17

### Fixed

- **Coach chat reload ordering** (`crud.py`, `routers/ai.py`) — chat history
  reloads now preserve user/assistant turn order for messages created in the
  same exchange instead of tie-breaking equal timestamps by UUID, preventing
  assistant replies from shifting under the wrong question after a page reload.

## [0.32.5] - 2026-06-17

### Fixed

- **Ride metric source-key dedupe** (`crud.py`, `models.py`,
  `alembic/versions/20260611_000001_add_activity_source_to_ride_metrics.py`,
  `alembic/versions/20260617_000001_dedupe_ride_metric_source_keys.py`) —
  imported activity metrics now upsert by `(user_id, activity_source,
  external_activity_id)`, stale duplicate source-key rows are cleaned before
  relying on the unique index, and ride-metrics history collapses any remaining
  duplicate visible rows.
- **Persisted Ask Trainer plan updates** (`routers/ai.py`, `schemas.py`) —
  `/ai/ask-trainer` now returns the persisted full `updatedPlan` after applying
  plan updates so clients can refresh from the authoritative stored plan.
- **Coach date grounding coverage** (`tests/test_ai_service_unit.py`) — added a
  June 17, 2026 Europe/Berlin prompt regression covering recent activity
  history, upcoming plan entries, and exact weekday/date labels.

## [0.32.4] - 2026-06-15

### Fixed

- **Coach upcoming-day interpretation** (`services/prompts.py`,
  `tests/test_ai_service_unit.py`) — Ask Trainer now treats "upcoming",
  "next", and "coming days" as today-and-future plan entries only, and must not
  answer those questions from historical plan days or call non-rest recovery
  sessions "pure rest".

## [0.32.3] - 2026-06-14

### Fixed

- **Coach date grounding** (`services/ai_service.py`, `services/prompts.py`,
  `tests/test_ai_service_unit.py`) — Ask Trainer prompts now include
  precomputed weekday/date labels and today/tomorrow annotations on plan days,
  and the coach is instructed to copy those labels instead of inventing weekday
  names from model memory.

## [0.32.2] - 2026-06-14

### Fixed

- **Activity summary refresh identifiers** (`crud.py`, `schemas.py`,
  `tests/test_crud.py`) — process-pending-feedbacks now accepts stable external
  activity IDs as well as numeric IDs, so Intervals/FIT-derived 64-bit activity
  identifiers are not lost to JavaScript number rounding before the backend
  looks up ride metrics.

## [0.32.1] - 2026-06-14

### Fixed

- **Activity summary prompt freshness** (`services/prompts.py`, `crud.py`,
  `tests/test_ai_service_unit.py`) — summaries generated for newly visible
  ride metrics now include activity names and treat the listed activities as
  authoritative, preventing stale assessment notes from causing "Your Recent
  Training Summary" to describe an older hike instead of the latest MTB ride.
  The prompt also marks the latest listed activity explicitly and requires the
  first summary bullet to anchor on it, with ride metric lookups ordered by date,
  start time, and activity id. Older free-form rider-assessment notes are no
  longer included in this refresh prompt, so stale notes cannot reintroduce an
  older activity. If the LLM returns an empty or incomplete summary, the backend
  now writes a deterministic fallback summary based on the newest ride metric.

## [0.32.0] - 2026-06-11

### Added

- **Source-neutral activity import contract** (`services/activity_imports.py`,
  `services/activity_sync.py`, `routers/ai.py`, `routers/strava.py`,
  `routers/users.py`, `services/intervals_service.py`) — normalizes Strava,
  Intervals.icu, and FIT imports into a shared imported-activity payload before
  ride-metric calculation while preserving the existing `strava_activity_id`
  dashboard contract.

- **Ride metric source metadata** (`models.py`, `crud.py`, `schemas.py`,
  `alembic/versions/20260611_000001_add_activity_source_to_ride_metrics.py`) —
  stores `activity_source`, `external_activity_id`, and optional source metadata
  on ride metrics, with centralized source/external-id lookup for dedupe.

- **Import normalization coverage** (`tests/test_activity_imports.py`,
  `tests/test_activity_sync.py`, `tests/test_intervals.py`,
  `tests/test_contract.py`) — verifies multiple sources use the same normalized
  contract and that duplicate detection still skips already-imported activities.

## [0.31.0] - 2026-06-10

### Added

- **Backend scheduler foundation** (`services/scheduler.py`, `main.py`,
  `services/plan_maintenance.py`, `services/activity_sync.py`) — adds a shared
  in-process recurring job registry with per-job duplicate-run protection,
  deterministic `run_once` support, structured lifecycle logs, and clean FastAPI
  lifespan startup/shutdown.

- **Scheduler documentation and coverage** (`README.md`, `tests/test_scheduler.py`)
  — documents the single-backend-replica Docker assumption, job registration
  pattern, current recurring jobs, and fake-sleep/direct-run testing approach.

## [0.30.0] - 2026-06-10

### Added

- **Backend activity-triggered plan updates** (`services/activity_sync.py`,
  `main.py`, `config.py`) — adds an in-process periodic activity sync that
  checks connected Strava and Intervals.icu sources with auto-sync enabled,
  imports newly detected activities, updates ride metrics, matches planned
  workouts, and triggers plan adaptation for matched rides without requiring a
  browser session.

- **Activity sync coverage** (`tests/test_activity_sync.py`) — covers disabled
  source skips, duplicate detection, independent Strava/Intervals cursors, and
  per-user/source failure isolation.

## [0.29.3] - 2026-06-10

### Fixed

- **Intervals.icu rounded cursor matching** (`routers/intervals.py`,
  `tests/test_intervals.py`) — accepts the small JavaScript number-rounding drift
  that can occur when 63-bit Intervals activity hashes are sent back as
  `after_id`, so newly returned activities are not discarded as a missing cursor.

## [0.29.2] - 2026-06-10

### Added

- **Intervals.icu import diagnostics** (`routers/intervals.py`, `routers/ai.py`,
  `routers/users.py`) — logs bounded activity samples, cursor-filter results,
  analysis upserts, history-import persistence, and ride-metrics-history
  responses so missing Intervals activities can be traced from API fetch through
  database visibility.

## [0.29.1] - 2026-06-10

### Fixed

- **Intervals.icu same-day activity fetches** (`routers/intervals.py`,
  `tests/test_intervals.py`) — Intervals activity list and history-import
  windows now use the app-local date and send tomorrow as the `newest` bound so
  activities completed today are included immediately instead of waiting until
  the next calendar day.

## [0.29.0] - 2026-06-09

### Added

- **Daily backend training-plan maintenance** (`services/plan_maintenance.py`,
  `main.py`, `crud.py`) — adds an in-process 02:00 app-timezone maintenance job
  that detects stale incomplete plan days for onboarded users and reuses the
  existing plan adaptation flow so schedules stay current without a browser login.

- **Plan maintenance coverage** (`tests/test_plan_maintenance.py`) — verifies
  stale-plan updates, no-op current plans, non-onboarded user skips, timezone
  cutoffs, idempotency, per-user failure isolation, and 02:00 scheduling math.

## [0.28.5] - 2026-06-09

### Fixed

- **Timestamped application logs** (`logging.yaml`) — root/application logger
  output now uses the same timestamped formatter as Uvicorn logs, so auth and
  validation warnings include dates in Docker output.

## [0.28.4] - 2026-06-09

### Fixed

- **JWT secret length validation** (`auth.py`, `.env.example`, `README.md`) —
  HS256 deployments now fail fast when `JWT_SECRET` is shorter than 32 bytes,
  while dev/test environments log one clear warning and suppress PyJWT's repeated
  per-request `InsecureKeyLengthWarning`.

## [0.28.3] - 2026-06-09

### Fixed

- **Intervals.icu analysis payload compatibility** (`routers/intervals.py`,
  `schemas.py`, `routers/ai.py`) — Intervals.icu activity summaries now include
  the required Strava-shaped numeric defaults, and Intervals-triggered analysis
  updates the Intervals sync cursor instead of Strava state.

- **Validation failure diagnostics** (`main.py`) — request validation errors now
  log the failing route and schema fields so future 422 responses can be diagnosed
  from container logs without recording request bodies.

## [0.28.2] - 2026-06-09

### Fixed

- **Timestamped backend Docker logs** (`entrypoint.sh`, `logging.yaml`) — Uvicorn
  access and application log lines now include full date/time stamps in container
  output, making API requests and health checks easier to correlate.

## [0.28.1] - 2026-06-08

### Fixed

- **Intervals.icu auto-sync migration compatibility**
  (`alembic/versions/20260608_000002_add_intervals_auto_sync_enabled.py`) — adds
  `users.intervals_auto_sync_enabled` in a follow-up migration so databases that
  already applied `20260608_000001` still receive the new column.

## [0.28.0] - 2026-06-08

### Added

- **Activity automatic sync preferences** (`models.py`, `schemas.py`, `routers/users.py`,
  `alembic/versions/20260608_000001_add_strava_auto_sync_enabled.py`,
  `alembic/versions/20260608_000002_add_intervals_auto_sync_enabled.py`) — adds
  persisted per-user `stravaAutoSyncEnabled` and `intervalsAutoSyncEnabled` flags that
  default to enabled for existing behavior while allowing the frontend to disable
  automatic polling for either activity source.

- **Intervals.icu activity importer** (`routers/intervals.py`, `services/intervals_service.py`,
  `models.py`, `crud.py`, `schemas.py`) — added per-user Intervals.icu API-key storage,
  connection management endpoints, manual recent-activity import, best-effort activity
  detail/stream fetching, deterministic import IDs, and ride metric upserts so Strava can
  remain optional.

- **Intervals.icu credentials migration** (`alembic/versions/20260607_000001_add_intervals_tokens.py`)
  — adds encrypted per-user Intervals.icu API key storage with athlete metadata.

- **Intervals.icu import tests** (`tests/test_intervals.py`) — covers credential setup,
  successful import, duplicate-safe re-import, auth failure handling, and summary-only
  fallback when streams are missing.

### Changed

- **Intervals.icu app-open sync parity** (`routers/intervals.py`, `models.py`, `schemas.py`) —
  adds an Intervals activity-list endpoint plus independent profile cursor fields so the
  frontend can poll for new Intervals activities the same way it polls Strava.

## [0.27.2] - 2026-06-05

### Fixed

- **Auto-matched rides with >2.5× duration mismatch now set `label_override = "Mismatch"`**
  (`services/ride_matching.py`, `crud.py`) — `apply_ride_plan_matches` detects gross
  duration divergence at match time and writes a `Mismatch` label override so the
  frontend bypasses score computation and shows a clear warning badge immediately.

## [0.27.1] - 2026-06-03

### Fixed

- **Missing Alembic revision restored** (`alembic/versions/20260530_000001_add_ride_label_override.py`)
  — restored the `20260530_000001` migration so deployed databases stamped with that
  revision can resolve the migration graph and continue upgrading normally.

## [0.27.0] - 2026-06-03

### Added

- **Bulk FIT import fallback** (`routers/users.py`, `schemas.py`) — added
  `POST /users/me/upload-fit/bulk` for multi-file Garmin/Wahoo/Zwift uploads with
  per-file imported, skipped, and failed results while preserving the existing single-file
  upload endpoint.

- **Normalized FIT ride metrics** (`routers/users.py`) — FIT imports now derive a stable
  synthetic activity id from FIT metadata/start time/fingerprint, skip duplicate uploads,
  parse richer record streams (watts, heart rate, cadence, altitude, speed, time, GPS),
  and feed imported activities into the existing ride metrics pipeline.

- **Bulk FIT contract coverage** (`tests/test_contract.py`) — added API tests for batch
  success, duplicate skipping, ride metric creation, and partial failure handling.

## [0.26.3] - 2026-06-01

### Added

- **Strava API 2026 impact note** (`docs/strava-api-2026-impact.md`) — summarizes
  Strava's June 2026 developer-program changes, the app's current Strava API usage,
  policy risks around AI processing, and migration action points for the 2027 base
  URL and OAuth revoke changes.

## [0.26.2] - 2026-05-28

### Fixed

- **Ride-to-plan date matching** (`services/ride_matching.py`, `routers/users.py`) —
  ride history now re-applies plan matching from each activity's own `activity_date`, so
  activities imported or analyzed later cannot keep a planned-day snapshot from the
  processing date.

- **Stale plan snapshot cleanup** (`services/ride_matching.py`) — mismatched stored plan
  snapshots are cleared when no plan entry exists for the actual activity date, while
  same-day rest-day context is preserved for display.

## [0.26.1] - 2026-05-26

### Fixed

- **Coach chat date awareness** (`services/dates.py`, `services/ai_service.py`,
  `services/prompts.py`) — ask-trainer prompts now include an authoritative local
  date context for today, yesterday, and tomorrow, including weekdays and ISO dates,
  so the coach does not infer conflicting calendar dates from model knowledge or
  conversation history.

- **Date-awareness regression coverage** (`tests/test_dates.py`,
  `tests/test_ai_service_unit.py`) — added focused tests for the generated date
  context and its injection into the ask-trainer system prompt.

## [0.26.0] - 2026-05-24

### Added

- **Weather enrichment for ride metrics** (`services/weather_service.py`, `models.py`,
  `schemas.py`, `crud.py`) — Strava activities now persist start coordinates plus
  Open-Meteo temperature, apparent temperature, condition, weather code, wind,
  precipitation, and source metadata on `ride_metrics`.

- **Alembic migration `20260524_000001_add_ride_weather`** — adds the weather and
  coordinate columns to `ride_metrics` while remaining idempotent for existing databases.

- **Weather-aware Strava import and analysis** (`routers/strava.py`, `routers/ai.py`,
  `services/strava_service.py`) — historical imports and new activity analysis request
  Strava GPS `latlng` streams and attach weather using the midpoint activity GPS point
  when available, falling back to Strava `start_latlng`; the lookup uses the activity
  midpoint date/time and switches to Open-Meteo archive data for older activities.
  Recent weather is included in activity analysis prompts.

- **Best-effort backfill for existing rides** (`routers/users.py`) — loading ride history
  attempts to fill missing weather for stored Strava rides by fetching activity details and
  querying Open-Meteo; failures are logged and do not block the dashboard.

### Changed

- **Weather-aware plan generation/adaptation** (`services/prompts.py`, `services/ai_service.py`,
  `routers/ai.py`) — upcoming forecast context near the athlete's usual activity location is
  passed into plan generation and adaptation so the coach can shorten hot-day sessions, add
  warmup/caution for cold days, or swap unsafe weather to indoor, recovery, or strength work.

## [0.25.2] - 2026-04-26

### Added

- **`X-Request-ID` middleware** (`main.py`) — every request is tagged with a correlation
  ID read from the incoming `X-Request-ID` header (client-generated) or a fresh UUID when
  absent; the ID is stored on `request.state.request_id` for route handlers and exception
  handlers, and is echoed back in the response header so browser logs and server logs can
  be correlated by ID.  CORS is updated to allow and expose the header.

- **Global `HTTPException` handler with structured logging** (`main.py`) — replaces the
  default FastAPI handler; logs method, path, status, and `request_id` at `WARNING` for
  4xx and `ERROR` for 5xx, then returns the same `{"detail": …}` JSON body.  Any
  `WWW-Authenticate` or other headers set by the raising code are preserved.

- **`recalculate-metrics` failure log** (`routers/users.py`) — `ValueError` rejections
  are now logged at `WARNING` with `user_id` and `request_id` before the 400 is raised.

- **Background Strava import failure log** (`routers/strava.py`) — unhandled exceptions
  in `_run_import_background` are now logged at `ERROR` with `user_id`, processed/total
  activity counts, and a full traceback (`exc_info=True`) before updating the progress
  state to `"error"`.

## [0.25.1] - 2026-04-25

### Fixed

- **Manual metric recalculation refreshes persisted ride feedback** (`services/metrics_service.py`)
  — rebuilding TSS, CTL, ATL, and TSB now also regenerates `rider_assessment.last_ride_feedback`
  from the latest recalculated ride so coach feedback stays aligned with the new FTP-based metrics.

- **Regression test for recalculation feedback refresh** (`tests/test_users.py`) — added an API
  test that confirms `POST /users/me/recalculate-metrics` updates the stored last-ride feedback
  after recalculation.

## [0.25.0] - 2026-04-25

### Added

- **`use_estimated_ftp` column on `User` model** (`models.py`) — boolean flag (default
  `False`) controlling whether AI-estimated or user-entered FTP is used for training-load
  computations (TSS, CTL, ATL, TSB).

- **Alembic migration `20260425_000001_add_use_estimated_ftp`** — adds the column to the
  `users` table with `server_default=false()`, backward-compatible with existing data.

### Changed

- **FTP priority for training-load calculations** (`routers/ai.py` `analyse_activities`)
  — user-entered FTP (`current_ftp`) is now the default for all TSS/CTL/ATL computations.
  Estimated FTP is only used when `use_estimated_ftp=True`. Previously estimated FTP
  always took precedence.

- **FTP priority in AI plan generation / chat** (`services/ai_service.py`) —
  `adapt_training_plan()` and `ask_trainer()` now check `profile["useEstimatedFTP"]` to
  decide which FTP value is passed to the AI prompt (user-entered first by default).

- **User schemas** (`schemas.py`) — `UserResponse`, `UpdateProfileRequest`, and
  `UserProfileSchema` gain `use_estimated_ftp` / `useEstimatedFTP` field.

- **Users router** (`routers/users.py`) — `_user_to_response()` maps the new field.

## [0.24.0] - 2026-04-25

### Added

- **`compute_readiness_recommendations`** (`services/analysis.py`) — new function that
  produces a list of short, actionable bullet-point tips based on the athlete's current
  CTL, ATL, TSB, overall readiness score, and days until race. Rules cover:
  - TSB/form bands (severe fatigue → very fresh) with specific training advice per band
  - CTL/fitness bands with base-building vs quality-focus guidance
  - Race-countdown phases (>21 days → build; 10–21 → taper; 3–10 → final taper; ≤3 → rest)
  - Overall score nudge when score is below the 65-point "Race Ready" threshold

- **`recommendations` field on `ReadinessScoreResponse`** (`schemas.py`) — `list[str]`
  field (default `[]`) carrying the output of `compute_readiness_recommendations`.

### Changed

- **`GET /ai/readiness-score`** (`routers/ai.py`) — imports and calls
  `compute_readiness_recommendations` and includes the result in every response.

## [0.23.0] - 2026-04-24

### Changed

- **Auto-rate new rides: planned type vs actual type comparison** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — when a new Strava activity is downloaded and
  matched against the training plan, the AI coach now explicitly compares what was *planned*
  against what was *actually performed*:

  - `rate_workout_system()` instructs the AI to first check whether the ride's character
    (e.g. endurance, threshold intervals, VO2max) matches the planned type, and to call out
    any mismatch before commenting on power/HR numbers.

  - `rate_workout_user()` accepts a new `actual_ride_analysis` parameter (dict produced by
    `build_ride_analysis`) and renders an *"Actual ride character (algorithmically derived)"*
    section into the prompt.  The section includes the detected ride category, average power,
    and a bullet for each detected interval block (duration, avg watts, % FTP, avg HR).

  - `rate_workout_user()` now accepts `feedback=None` without crashing — auto-rate calls
    that have no user-entered feedback are handled gracefully.

  - `rate_completed_workout()` in `ai_service.py` gains a `ride_analysis: dict | None`
    keyword argument and forwards it to `rate_workout_user()`.

  - `_auto_rate_ride()` in `routers/ai.py` now calls `build_ride_analysis(streams, ftp)` and
    passes the result as `ride_analysis` so every auto-generated coach note reflects the true
    ride character.

## [0.22.0] - 2026-04-24

### Changed

- **`POST /ai/ask-trainer` — reduced response latency** — two optimisations cut the
  number of sequential LLM round trips the user waits for:

  1. **`classify_question` parallelised with DB fetches** — the question-classification
     call now runs concurrently with the `get_ride_metrics_history` database query instead
     of after it, hiding the 1–2 s classification latency behind an already-necessary DB
     operation.

  2. **`update_coach_memory` moved to a `BackgroundTask`** — coach-memory updates are
     now written after the HTTP response is sent rather than before it.  A dedicated
     `_update_memory_bg` helper manages its own database session via `async_session_maker`
     and swallows all errors gracefully (rate-limit and general exceptions are logged but
     never surfaced to the caller), preserving the existing best-effort semantics.

## [0.21.0] - 2026-04-22

### Added

- **`threshold_heart_rate` in `POST /users/me/estimate-ftp`** — the endpoint now accepts an
  optional `threshold_heart_rate` (bpm) alongside `max_heart_rate` and `resting_heart_rate`.
  When provided the value is immediately persisted to the user profile so subsequent FTP
  estimation and metric snapshots include the athlete's lactate-threshold HR.  The
  `EstimateFTPRequest` schema (`schemas.py`) has been updated accordingly.

- **Threshold HR input in Settings page** (`frontend/src/pages/SettingsPage.tsx`) — a third
  heart-rate field ("Threshold Heart Rate") is now shown in the HR Settings section, with a
  helper note explaining it as the lactate-threshold HR (~87 % of max HR).  The current stored
  value is displayed alongside max and resting HR.  The "Save & Estimate FTP" button is enabled
  when any of the three HR fields (or age) contains a value.

- **Metrics history refresh after recalculate** (`frontend/src/pages/SettingsPage.tsx`) — after
  `handleConfirmFTP()` and `handleRecalculate()` complete successfully, `fetchMetricsHistory()`
  is called and the result written to the Zustand store.  This means the FTP progression chart on
  the Dashboard updates immediately without requiring a page reload.

### Changed

- **`POST /users/me/recalculate-metrics` — per-ride `AthleteMetricSnapshot` creation** — previously
  the endpoint deleted all historical metric snapshots and replaced them with a single final
  snapshot (CTL/ATL/TSB/FTP values at the end of the last ride).  It now creates **one back-dated
  snapshot per ride** (`source = "manual_recalculate"`, `recorded_at` set to the ride date).  This
  preserves a full time-series for the FTP/CTL/ATL progression chart after any manual
  recalculation.

- **`estimateFTP()` frontend service** (`frontend/src/services/user.ts`) — now forwards
  `thresholdHeartRate` to the backend in the request body.

### Fixed

- A `ValueError` when parsing a malformed `activity_date` inside `recalculate_metrics()` now
  emits a warning log entry (including the activity ID) instead of silently using the current
  timestamp without any diagnostic information.

## [0.20.0] - 2026-04-22

### Added

- **`GET /users/me/ride-metrics-history`** — new endpoint that returns the most recent
  90 per-ride CTL/ATL/TSB records in chronological (oldest-first) order, sourced from
  the `ride_metrics` table.  Unlike the sparse `AthleteMetricSnapshot` data (one point
  per analysis run), this gives one data point per ride so expert users can see the full
  time series of how their training load develops.

- **`RideMetricHistoryResponse` schema** (`schemas.py`) — wraps a list of existing
  `RideMetricSchema` objects (`rides: list[RideMetricSchema]`).

- **Backend tests** (`tests/test_users.py`): three new test cases for the new endpoint —
  empty list for a new user; populated list with CTL/ATL/TSB after analyse-activities;
  unauthenticated access rejected with HTTP 401.


### Added

- **`POST /users/me/estimate-ftp`** — new endpoint that saves `max_heart_rate` and/or
  `resting_heart_rate` to the user profile and returns the best available FTP estimate.
  Priority order: most recent `AthleteMetricSnapshot` → `RiderAssessment.estimated_ftp` →
  `User.current_ftp` → `null`.  When `resting_heart_rate` has never been set it is
  automatically defaulted to 60 bpm.  Response includes a `source` field
  (`"ftp_estimation"`, `"strava_analysis"`, `"rider_assessment"`, `"profile"`, or `"none"`)
  so the frontend can give the user meaningful context.

- **`EstimateFTPRequest` / `EstimateFTPResponse` schemas** (`schemas.py`) — both fields of
  the request (`max_heart_rate`, `resting_heart_rate`) are optional.  The response carries
  `estimated_ftp` (nullable int) and a `source` string.

- **Backend tests** (`tests/test_users.py`): four new test cases covering the new endpoint:
  - no data returns `null` FTP with source `"none"`;
  - HR values are persisted on the user profile;
  - missing `resting_heart_rate` is defaulted to 60;
  - existing `AthleteMetricSnapshot` FTP is returned correctly;
  - unauthenticated requests are rejected with HTTP 401.



### Changed

- **`estimate_ftp_over_time()` — improved algorithm** (`services/analysis.py`):

  - **Power-only fallback fixed** — replaced the incorrect `0.95 × steady_5-min_segment_power`
    formula (which could wildly underestimate FTP for sub-threshold efforts) with the standard
    FTP-test protocol: `best_20_min_power × 0.95`.  Rides shorter than 20 minutes are skipped
    in this path since a meaningful 20-min max effort cannot be produced from them.

  - **Smoothing changed from EWMA to 3-week sliding-window max** — for each date point the
    smoothed FTP is now the *maximum* raw estimate from any ride in the preceding 21 days
    (``smoothing_days`` default changed from 42 to 21).  Taking the max rather than an
    exponential average means a single strong ride correctly propagates while easy / recovery
    rides — which produce lower estimates — no longer drag the series down.  FTP reflects
    current capability, not an average of all recent efforts.

  - New private helper `_in_window(date_str, start, end)` used by the sliding-window loop.

## [0.18.0] - 2026-04-22

### Added

- **FTP estimation over time** (`services/analysis.py`) — new `estimate_ftp_over_time()`
  function identifies rides with steady-state power intervals (coefficient of variation < 15 %),
  estimates FTP for each using the existing HR-corrected formula (or a power-only fallback), then
  applies an exponential weighted moving average (42-day time constant) so that the resulting FTP
  series changes gradually.  Returns a list of `{date, ftp, raw_ftp}` dicts ordered by ride date.

- **FTP estimation during activity ingestion** — `estimate_ftp_over_time` is now called inside
  `POST /ai/analyse-activities` and the Strava background import task (`_run_import_background`).
  Per-ride smoothed FTP estimates are persisted as `AthleteMetricSnapshot` rows with
  `source = "ftp_estimation"` and `recorded_at` set to the ride date so they appear correctly
  on the Athlete Progression chart.

- **`POST /users/me/recalculate-metrics`** — new endpoint that rebuilds the full TSS / CTL / ATL /
  TSB chain for all stored rides using a given FTP value.  Accepts an optional `ftp_override`
  (watts); if omitted, the user's current FTP is used.  Clears all previous
  `athlete_metric_snapshots` and writes a fresh summary snapshot.  Used by the new Settings UI.

- **`crud.delete_athlete_metric_snapshots()`** — deletes all AthleteMetricSnapshot rows for a
  user, used before rebuilding the metric history.

- **`crud.get_all_ride_metrics_ordered()`** — returns all RideMetric rows for a user sorted
  ascending by date, used during full-chain recalculation.

- **`crud.create_athlete_metric_snapshot()` — `recorded_at` parameter** — the existing function
  now accepts an optional `recorded_at: datetime` argument so back-dated FTP-estimation snapshots
  can be inserted with the correct ride date.

### Changed

- `strava._run_import_background` now accepts `max_heart_rate` and `resting_heart_rate` from the
  user profile and passes them to `estimate_ftp_over_time` so HR-based correction is applied when
  the athlete's max HR is on record.



### Added

- **Per-ride time-series metrics pipeline** — a structured `ride_metrics` table now stores TSS,
  normalised power, intensity factor, CTL/ATL/TSB, FTP used, ride purpose, rule-based summary,
  and coach/user notes for every ride. This replaces plan-estimate-based fitness tracking with
  values derived from actual completed rides.
  - `models.py` — new `RideMetric` ORM model; `User` now has a `ride_metrics` relationship.
  - `alembic/versions/20260421_000001_add_ride_metrics.py` — idempotent migration creating the
    `ride_metrics` table with a unique index on `(user_id, strava_activity_id)`.
  - `services/analysis.py` — four new pure functions: `compute_ride_tss()`,
    `apply_ctl_atl_decay()` (exponential weighted average with multi-day gap handling),
    `build_rule_based_summary()`, and `build_ride_metrics_chain()`.
  - `crud.py` — five new async functions: `upsert_ride_metric()`, `get_ride_metrics_history()`,
    `get_latest_ride_metric()`, `update_ride_metric_notes()`, `get_ride_metric_by_date()`.
  - `schemas.py` — `RideMetricSchema` and `ImportHistoryResponse` schemas.

- **Historical Strava import endpoint** (`POST /strava/import-history`) — fetches up to 6 months
  of Strava activities (configurable via `?months=` query param, clamped 1–24), computes the full
  CTL/ATL/TSB chain, and bulk-upserts ride metrics. Safe to call repeatedly; does not overwrite
  existing coach or user notes.

- **Ride metrics context injected into all AI prompts** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — `ride_metrics_context_section()` renders the 30
  most recent rides as a compact table (date | purpose | TSS | NP | CTL | ATL | TSB | summary +
  notes). This section is passed to `generate-plan`, `adapt-plan`, and `ask-trainer` so the LLM
  reads pre-computed structured data instead of re-processing raw activities.

- **Coach auto-rating at ingestion** — when `POST /ai/analyse-activities` processes a ride that
  matches a plan day, `rate_completed_workout()` is called automatically and the result is stored
  as `coach_note` on the `RideMetric` row.

- **User feedback inference via chat** (`routers/ai.py`) — the `ask-trainer` endpoint now
  extracts a `ride_note_update` field from the LLM response. If the user mentions how a ride felt
  in conversation, the inferred note is persisted as `user_note` on the matching `RideMetric` row
  without any extra user action.

### Changed

- **`POST /strava/import-history` defaults to 6 months** — the `after` timestamp is sent directly
  to Strava's API so only recent activities are downloaded; no full-history pagination needed.
- **`rate_completed_workout()` guard fix** (`services/ai_service.py`) — changed early-return
  condition from `if not feedback` to `if not feedback and not stream_delta` so the coach can
  auto-rate from stream data alone without requiring user-entered feedback.
- **`rate_workout` endpoint cleanup** (`routers/ai.py`) — inline auto-adapt block replaced with
  the shared `_auto_adapt_plan()` helper.

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
