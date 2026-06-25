# Graph Report - ai-trainer  (2026-06-25)

## Corpus Check
- 193 files · ~164,320 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2653 nodes · 5054 edges · 143 communities (128 shown, 15 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 238 edges (avg confidence: 0.76)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ce719086`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 126|Community 126]]
- [[_COMMUNITY_Community 140|Community 140]]
- [[_COMMUNITY_Community 141|Community 141]]
- [[_COMMUNITY_Community 142|Community 142]]
- [[_COMMUNITY_Community 149|Community 149]]

## God Nodes (most connected - your core abstractions)
1. `User` - 99 edges
2. `useAppStore` - 71 edges
3. `CamelModel` - 61 edges
4. `_make_user()` - 60 edges
5. `apiFetch()` - 46 edges
6. `RideMetric` - 42 edges
7. `decode_token()` - 37 edges
8. `ask_trainer_system()` - 31 edges
9. `TrainingDay` - 29 edges
10. `ask_trainer_plan_updates_rule()` - 25 edges

## Surprising Connections (you probably didn't know these)
- `test_intervals_import_auth_failure_sets_error()` --calls--> `decode_token()`  [INFERRED]
  backend/tests/test_intervals.py → backend/auth.py
- `test_intervals_import_summary_only_when_streams_missing()` --calls--> `decode_token()`  [INFERRED]
  backend/tests/test_intervals.py → backend/auth.py
- `test_disabled_memory_not_returned_in_prompt_facts()` --calls--> `decode_token()`  [INFERRED]
  backend/tests/test_memory_privacy.py → backend/auth.py
- `test_get_me_includes_strava_connection()` --calls--> `decode_token()`  [INFERRED]
  backend/tests/test_users_extended.py → backend/auth.py
- `test_availability_constraints_block_training_plan_updates()` --calls--> `_filter_plan_updates_for_availability_constraints()`  [INFERRED]
  backend/tests/test_ai_service_unit.py → backend/routers/ai.py

## Import Cycles
- None detected.

## Communities (143 total, 15 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (126): Unit tests for services/ai_service.py helper functions., When targetPower is given, TSS should use mid-point for IF calculation., Score and component scores must be in [0, 100]., TSB near +10 with high CTL should produce a high score., TSB ≤ -30 should give form_score 0., Zero CTL and zero ATL (TSB=0) → form_score=50, fitness_score=0, score=32.5., thinking' must be stripped from the return value and never reach the frontend., When classify says needs_science_rag=False, the effective science context is emp (+118 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (76): _activity_sport_type(), adapt_training_plan(), AIResponseFormatError, analyse_fit_activity(), analyse_strava_activities(), ask_trainer(), batch_review_rides(), _chat() (+68 more)

### Community 2 - "Community 2"
Cohesion: 0.04
Nodes (78): AdaptPlanRequest, AIKeySaveRequest, AIKeyStatusSchema, AnalyseActivitiesRequest, AnalyseActivitiesResponse, AskTrainerRequest, AskTrainerResponse, AthleteAvailabilityConstraintSchema (+70 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (45): User, AthleteContextRequest, AthleteContextSchema, MemoryPrivacySettingsSchema, RaceEventResponse, UserResponse, WorkoutLogRequest, Response (+37 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (44): AIChat(), ChatExchange, groupMessagesIntoExchanges(), MARKDOWN_COMPONENTS, Props, Props, WORKOUT_COLORS, effortLabels (+36 more)

### Community 5 - "Community 5"
Cohesion: 0.09
Nodes (65): AsyncSession, db(), _make_ride(), _make_user(), Unit tests for the crud data-access layer.  These tests call crud functions dire, Yield a fresh session that is rolled back after each test., test_chat_messages_ordered_by_timestamp(), test_chat_messages_with_same_timestamp_keep_insert_order() (+57 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (55): AIKeySettings(), Provider, PROVIDERS, formatDate(), mockConfirm, mockDelete, mockFetch, mockUpdate (+47 more)

### Community 7 - "Community 7"
Cohesion: 0.07
Nodes (45): ask_trainer_plan_updates_rule(), ask_trainer_system(), Return the planUpdates rule string for the ask_trainer system prompt., recommendation_reasoning_layers_rule(), rest_recommendation_rules(), The ask_trainer system prompt must contain outlook handling instructions., Coach must surface a personal-context call when the layers diverge., ask_trainer_system must include response quality rules for the response field. (+37 more)

### Community 8 - "Community 8"
Cohesion: 0.04
Nodes (60): AthleteMemoryFact, _activity_key_filters(), _as_aware_utc(), _clamp_confidence(), create_race_event(), create_user(), deactivate_expired_availability_constraints(), delete_all_ride_metrics() (+52 more)

### Community 9 - "Community 9"
Cohesion: 0.07
Nodes (47): datetime, _activities_are_temporally_contained(), activity_family(), activity_identity_tokens(), _activity_interval(), activity_names_compatible(), are_near_duplicate_activities(), normalize_activity_text() (+39 more)

### Community 10 - "Community 10"
Cohesion: 0.03
Nodes (51): make_flat_stream(), rate-workout endpoint must return flag_for_adaptation=false for normal sessions., When flag_for_adaptation=True, adapt_training_plan must be called automatically., classify_question is called; RAG is NOT called when needs_science_rag=False., When there are no unreviewed rides the endpoint returns an empty review., Without context_workout the prompt must use the 'explicit request only' planUpda, rate-workout endpoint must return follow-up fields when the AI flags ambiguous r, rate-workout endpoint must return falsy follow-up fields for normal high-confide (+43 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (22): async_sessionmaker, AsyncOpenAI, _chunk_text(), _embed_batch(), _fetch_s2_papers(), _get_encoder(), ingest_seed_corpus(), ingest_semantic_scholar() (+14 more)

### Community 12 - "Community 12"
Cohesion: 0.07
Nodes (34): get_effective_ftp(), _last_ride_recommendation(), Return the best available FTP value for *user*.      Priority order: explicit *f, Coverage tests for services/llm.py and services/metrics_service.py.  Covers: - g, OpenAIProvider.chat with json_mode=True passes response_format., OpenAIProvider.chat_history passes conversation messages to the API., GeminiProvider.chat raises AIRateLimitError when the API returns 429., GeminiProvider.chat returns text from the Gemini API on success. (+26 more)

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (46): TokenResponse, Path, admin_delete_user(), _admin_enabled(), admin_login(), admin_users(), AdminLoginRequest, AdminUsersResponse (+38 more)

### Community 14 - "Community 14"
Cohesion: 0.13
Nodes (49): HTTPException, Request, _active_availability_constraints_for_prompt(), adapt_plan(), analyse_activities(), _analysis_activity_log_sample(), ask_trainer(), _auto_adapt_plan() (+41 more)

### Community 15 - "Community 15"
Cohesion: 0.04
Nodes (45): dependencies, date-fns, lucide-react, react, react-dom, react-markdown, react-router-dom, @tailwindcss/forms (+37 more)

### Community 16 - "Community 16"
Cohesion: 0.04
Nodes (42): Frontend/backend API contract integration tests.  These tests simulate the exact, POST /users/me/upload-fit/bulk imports multiple FIT files in one request., Bulk FIT upload reports duplicate and invalid files without aborting the batch., GET /users/me must return every camelCase key that BackendUserResponse     (fron, The frontend StravaActivity interface uses snake_case property names     (moving, The backend should accept camelCase aliases too (used in the existing test suite, GET /users/me/plan and PUT /users/me/plan must use the { plan: [...] }     envel, POST /users/me/workouts/{date} must accept the WorkoutFeedbackSchema     camelCa (+34 more)

### Community 17 - "Community 17"
Cohesion: 0.11
Nodes (28): Any, FitUploadResponse, FitBulkUploadResponse, FitUploadFileResult, _analyse_fit_import(), _as_datetime(), _as_float(), _as_int() (+20 more)

### Community 18 - "Community 18"
Cohesion: 0.07
Nodes (36): get_athlete_context(), get_training_plan(), get_workout_log_by_date(), get_workout_logs(), list_active_availability_constraints(), Return the TrainingPlan for a user, or None., Create or replace the training plan for a user and flush., Return all WorkoutLog rows for a user. (+28 more)

### Community 19 - "Community 19"
Cohesion: 0.09
Nodes (28): DummyResponse, FakeAsyncHttpClient, Extended Strava router tests: callback, refresh, fetch_activity_streams., A state that has already expired should redirect with an error., Valid state referencing a non-existent user should redirect with an error., A full successful callback should redirect with success=true and persist the tok, A failed token exchange should redirect with an error message., When a Strava token already exists it should be updated, not duplicated. (+20 more)

### Community 20 - "Community 20"
Cohesion: 0.05
Nodes (28): GET /metrics-history returns an empty list for a new user., analyse-activities should create a per-ride metric visible in /ride-metrics-hist, estimate-ftp on a brand-new account with no rides returns null FTP., estimate-ftp returns FTP from the user profile (current_ftp)., recalculate-metrics should create one AthleteMetricSnapshot per ride, not just o, GET /ride-metrics-history returns an empty list for a new user., After analyse-activities, ride-metrics-history should include per-ride CTL/ATL/T, PATCH ride-feedback saves structured feedback as a formatted user_note. (+20 more)

### Community 21 - "Community 21"
Cohesion: 0.09
Nodes (25): LineChart(), LineChartProps, AthleteTraitsSettings(), FitFileUpload(), FitFileUploadProps, buildDefaults(), DEFAULT_FTP, FitnessMetricsCard() (+17 more)

### Community 22 - "Community 22"
Cohesion: 0.07
Nodes (24): Props, useFeedbackDebounce(), RecalcResult, baseProfile, {
  mockFetchCurrentUser,
  mockFetchMetricsHistory,
  mockFetchRideMetricsHistory,
  mockRecalculateMetrics,
  mockUpdateCurrentUser,
}, UseMetricsPipelineResult, baseProfile, {
  mockGenerateTrainingPlan,
  mockAnalyseStravaActivities,
  mockUpdateCurrentUser,
  mockSaveTrainingPlan,
  mockGetStravaActivities,
  mockGetStravaAuthUrl,
  mockDisconnectStrava,
} (+16 more)

### Community 23 - "Community 23"
Cohesion: 0.06
Nodes (51): get_all_ride_metrics_ordered(), get_latest_ride_metric(), get_near_duplicate_ride_metric(), get_ride_metric_by_date(), get_ride_metric_by_source(), get_ride_metric_by_strava_id(), get_ride_metrics_history(), get_unreviewed_ride_metrics() (+43 more)

### Community 24 - "Community 24"
Cohesion: 0.08
Nodes (23): AuthShell(), AuthShellProps, highlights, AuthCallbackPage(), { mockGetSessionToken, mockLoadUserData }, mockNavigate, LoginPage(), createTestQueryClient() (+15 more)

### Community 25 - "Community 25"
Cohesion: 0.08
Nodes (29): FakeRideMetric, Tests for the next-ride recommendation feature (Task 6).  Covers: - ``recommend_, Service returns 'recovery' recommendation with plan update., Service returns 'easier' recommendation., Service returns 'move_intensity' recommendation., Service handles empty rides list gracefully., Service handles LLM response with missing planUpdates key., Prompt should let personal context override physiologically similar options. (+21 more)

### Community 26 - "Community 26"
Cohesion: 0.07
Nodes (31): Tests for per-task model defaults and provider model selection.  Covers: - Confi, _resolve_model returns the correct model for each Gemini task., Unknown task names fall back to the module-level default constants., Unknown provider names fall back to OPENAI_MODEL., OpenAIProvider created for TASK_COACH uses the configured coach model., OpenAIProvider created for TASK_CLASSIFY uses the cheap classify model., GeminiProvider created for a task uses the configured model., When falling back to an available provider, it still uses the task model. (+23 more)

### Community 27 - "Community 27"
Cohesion: 0.07
Nodes (30): IntervalsConnect(), {
  mockDisconnectIntervals,
  mockSaveIntervalsConnection,
}, StravaConnect(), StravaConnectProps, { mockGetStravaAuthUrl, mockDisconnectStrava }, AnalysisStatus, UseStravaSyncResult, FormData (+22 more)

### Community 28 - "Community 28"
Cohesion: 0.11
Nodes (29): _backfill_ride_weather_bg(), Background weather backfill so dashboard hydration reads stored data immediately, fetch_activity_detail(), fetch_activity_streams(), Fetch per-second timeseries streams for a single Strava activity.      Returns a, Fetch a single Strava activity summary/detail payload., _activity_hour(), _activity_midpoint_datetime() (+21 more)

### Community 29 - "Community 29"
Cohesion: 0.15
Nodes (21): date, _event_date(), _next_race_date(), app_date_context(), app_timezone(), app_today(), app_today_stamp(), _date_label() (+13 more)

### Community 30 - "Community 30"
Cohesion: 0.09
Nodes (21): Tests for the cycling science RAG service (services/rag.py)., When RAG retrieval returns sources, the HTTP response must include them., When RAG retrieval returns no sources, the response sources list is empty/null., science_context returned by RAG must be forwarded to ai_service.ask_trainer., SQLite does not support pgvector; retrieval must return empty results., Authenticated request must queue the ingestion background task and return 200., Unauthenticated request must be rejected with HTTP 401., When OPENAI_API_KEY is absent the endpoint must return HTTP 503. (+13 more)

### Community 31 - "Community 31"
Cohesion: 0.04
Nodes (46): AIRateLimitError, Raised when the AI provider returns a rate-limit (429) response., _chat_history must propagate AIRateLimitError raised by the LLM provider., The /ask-trainer endpoint must return HTTP 503 when AIRateLimitError is raised., The /analyse-activities endpoint must return HTTP 503 when AIRateLimitError is r, The /generate-plan endpoint must return HTTP 503 when AIRateLimitError is raised, The endpoint must return HTTP 503 when AIRateLimitError is raised., Extra coverage tests for routers/ai.py.  Exercises endpoints and helpers that ar (+38 more)

### Community 32 - "Community 32"
Cohesion: 0.06
Nodes (35): ChatHistoryResponse, CoachMemoryRequest, CoachMemoryResponse, ImportHistoryResponse, LoginRequest, MetricsHistoryResponse, _password_personal_fragments(), PlanRequest (+27 more)

### Community 33 - "Community 33"
Cohesion: 0.06
Nodes (32): Layout(), navItems, StravaImportSummaryProps, failureSignature(), hasSameProgress(), INITIAL_PROGRESS, listeners, normalizeProgress() (+24 more)

### Community 34 - "Community 34"
Cohesion: 0.07
Nodes (27): update_memory_system(), System prompt must guide the AI to capture schedule constraints., System prompt must guide the AI to capture subjective fatigue / intensity respon, System prompt must guide the AI to retain actionable hydration/fueling context., System prompt must guide the AI to capture preferred workout types., System prompt must guide the AI to capture recurring coaching issues., System prompt must capture durable psychological training patterns., System prompt must guide the AI to record FTP history. (+19 more)

### Community 35 - "Community 35"
Cohesion: 0.12
Nodes (13): DAYS, emptyForm(), formatIsoDate(), Props, RaceEventForm, formatIsoDate(), {
  mockCreateRaceEvent,
  mockDeleteRaceEventRemote,
  mockUpdateRaceEventRemote,
}, mockNavigate (+5 more)

### Community 37 - "Community 37"
Cohesion: 0.08
Nodes (25): app_today_iso(), Return today's ISO date in the athlete-facing application timezone., athlete_context_section(), athlete_memory_facts_section(), next_ride_recommendation_user(), process_pending_feedbacks_system(), process_pending_feedbacks_user(), Build the user message for a next-ride recommendation.      *rides* is a list of (+17 more)

### Community 38 - "Community 38"
Cohesion: 0.09
Nodes (15): Tests for bring-your-own-key (BYOK) AI provider credential management.  Covers:, A key that produces a successful chat response → {"ok": true}., A key that causes an exception → 422., When the user has a key set, the BYOK ContextVar contains that key during AI req, With allow_admin_ai_key_fallback=False and no user key → 402., When a user has their own key, it is used even if global settings has one., Saving a key for a provider also switches the user's active provider., The stored key is never returned in the status response. (+7 more)

### Community 40 - "Community 40"
Cohesion: 0.19
Nodes (18): _activity_id_matches_cursor(), _activity_log_entry(), _activity_log_sample(), _activity_response(), _activity_response_log_sample(), delete_intervals_connection(), get_intervals_activities(), get_intervals_connection() (+10 more)

### Community 41 - "Community 41"
Cohesion: 0.09
Nodes (11): Coverage tests for routers/users.py endpoints not covered by test_users.py.  Cov, Returns estimated_ftp=None and source='none' when no FTP is set., Returns current_ftp when it is set on the user profile., POST /workouts/{date} persists a log; GET /workouts returns it., recalculate-metrics returns 400 when no FTP is available., recalculate-metrics succeeds when ftp_override is provided., test_estimate_ftp_with_current_ftp(), test_estimate_ftp_with_no_current_ftp() (+3 more)

### Community 42 - "Community 42"
Cohesion: 0.06
Nodes (48): baseDay, mockNavigate, typeColors, WorkoutCard(), activityFamily(), activityIdentityTokens(), activityNamesCompatible(), activityStartDistanceMs() (+40 more)

### Community 43 - "Community 43"
Cohesion: 0.27
Nodes (11): _first_float(), _first_int(), _first_str(), _latlng_points(), map_activity_to_imported_activity(), map_activity_to_ride_input(), _numeric_list(), Intervals.icu API client and activity mapping helpers. (+3 more)

### Community 44 - "Community 44"
Cohesion: 0.29
Nodes (12): ActivitySyncResult, fetch_recent_strava_activities(), _intervals_cursor_matches(), _persist_and_adapt(), _provider(), Backend-owned activity sync and plan adaptation., run_activity_sync(), _sanitize_strava_streams() (+4 more)

### Community 45 - "Community 45"
Cohesion: 0.21
Nodes (14): buildRideFeedbackChatMessages(), intentLabels, LegsFeeling, legsLabels, matchLabels, PlanMatchFeedback, Props, RideFeedbackForm() (+6 more)

### Community 46 - "Community 46"
Cohesion: 0.11
Nodes (18): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection, moduleResolution (+10 more)

### Community 47 - "Community 47"
Cohesion: 0.15
Nodes (18): _default_provider(), Return the best available provider based on configured API keys., _make_user(), Tests for AI provider selection logic in routers/ai.py., When GEMINI_API_KEY is set and user has no explicit preference, gemini is used., Return a lightweight mock with only the ``ai_provider`` attribute., User prefers gemini but gemini_api_key is not set → fall back to default., User prefers openai but openai_api_key is not set → fall back to default. (+10 more)

### Community 48 - "Community 48"
Cohesion: 0.16
Nodes (13): intervals_activity_id(), Return a stable signed-bigint-safe id for an Intervals.icu activity., _create_user(), _day(), _get_user(), test_activity_sync_imports_strava_and_keeps_intervals_cursor(), test_activity_sync_isolates_per_user_source_failures(), test_activity_sync_skips_disabled_intervals_source() (+5 more)

### Community 49 - "Community 49"
Cohesion: 0.08
Nodes (28): decode_token(), Return user_id or raise HTTP 401., A single unreviewed ride triggers a batch review and marks it as reviewed., Multiple unreviewed rides must all be passed to batch_review_rides and then mark, After rides are reviewed a second call to the endpoint returns nothing new., test_analyse_activities_auto_matches_single_planned_ride(), test_analyse_activities_marks_multiple_same_day_rides_ambiguous(), test_review_new_rides_multiple_rides_all_included() (+20 more)

### Community 50 - "Community 50"
Cohesion: 0.11
Nodes (17): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 51 - "Community 51"
Cohesion: 0.09
Nodes (33): best_n_min_power(), _best_power_points(), compute_ftp_from_streams(), compute_hr_drift(), compute_readiness_recommendations(), compute_readiness_score(), _critical_power_from_points(), _detect_intensity_spikes() (+25 more)

### Community 52 - "Community 52"
Cohesion: 0.10
Nodes (20): rate_workout_system(), rate_workout_system must embed COACH_PERSONA., rate_workout_system() prompt must include the follow-up dialogue instructions., rate_workout_system() must still mention feedback and flag_for_adaptation fields, rate_workout_system must include response quality rules for the feedback field., rate_workout_system must contain an example for a short recovery spin., rate_workout_system must contain an example for an over-paced endurance ride., rate_workout_system must contain an example for a missed or aborted workout. (+12 more)

### Community 53 - "Community 53"
Cohesion: 0.15
Nodes (13): ai_key_not_configured_handler(), http_exception_handler(), lifespan(), Log request validation failures without echoing request bodies into logs., Attach a correlation ID to every request/response cycle.      Reads the incoming, Log every HTTP error with correlation metadata, then return the standard respons, request_id_middleware(), validation_exception_handler() (+5 more)

### Community 54 - "Community 54"
Cohesion: 0.17
Nodes (15): ScriptDirectory, _get_script_dir(), Tests that verify the Alembic migration chain is well-formed.  These tests do no, Each revision ID must be unique within the migration history., Boolean columns must use 'true'/'false' as server_default, not integers.      Po, The Intervals auto-sync column must not be added only by an edited old revision., Alembic must see exactly one head revision.      Multiple heads cause ``alembic, Every revision must have at most one parent (no branch points).      A branching (+7 more)

### Community 55 - "Community 55"
Cohesion: 0.13
Nodes (12): Protocol, _attribute_int(), _gemini_total_tokens(), GeminiProvider, _get_provider_global(), LLMProvider, _openai_total_tokens(), OpenAIProvider (+4 more)

### Community 56 - "Community 56"
Cohesion: 0.12
Nodes (12): Extended user endpoint tests: coach memory, strava connection in profile., A JWT with a past expiry should be rejected with 401., A completely garbage token should be rejected with 401., A valid JWT whose user has since been deleted should return 401., A request with no Authorization header should return 401., After an analysis, the user profile should include the rider assessment., test_expired_token_returns_401(), test_get_me_includes_rider_assessment() (+4 more)

### Community 57 - "Community 57"
Cohesion: 0.17
Nodes (12): create_admin_token(), get_authelia_user(), get_current_user(), _get_or_create_authelia_user(), hash_password(), Password hashing, JWT creation/verification, and FastAPI auth dependency., Return a short-lived JWT that grants admin panel access., FastAPI dependency — raises 401/403 unless the request carries a valid admin JWT (+4 more)

### Community 58 - "Community 58"
Cohesion: 0.22
Nodes (15): RedirectResponse, _backend_url(), disconnect_strava(), _frontend_url(), get_import_progress(), Strava OAuth and activity proxy routes., Return the current background import progress for the authenticated user., RefreshRequest (+7 more)

### Community 59 - "Community 59"
Cohesion: 0.19
Nodes (14): batch_review_user(), Build the user message for a batch ride review.      *rides* is a list of RideMe, _FakeRide, Minimal duck-typed RideMetric for prompt tests., test_batch_review_rides_calls_chat_with_all_rides(), test_batch_review_rides_single_ride_still_works(), test_batch_review_user_contains_all_ride_dates(), test_batch_review_user_empty_rides() (+6 more)

### Community 61 - "Community 61"
Cohesion: 0.16
Nodes (15): build_last_ride_feedback(), Training metrics recalculation service.  Extracts the CTL/ATL/TSB chain-rebuild, Recompute TSS/CTL/ATL/TSB for all stored rides.      When *ftp_override* is give, _rebuild_metric_snapshots(), _recalculate_metric_chain(), recalculate_metrics_for_user(), _refresh_rider_assessment_feedback(), Falls back gracefully when no power or load metrics are present. (+7 more)

### Community 62 - "Community 62"
Cohesion: 0.21
Nodes (12): BasicAuth, Exception, fetch_activity_detail(), fetch_activity_streams(), fetch_recent_activities(), intervals_auth(), IntervalsAPIError, IntervalsAuthError (+4 more)

### Community 63 - "Community 63"
Cohesion: 0.12
Nodes (15): apply_ctl_atl_decay(), build_ride_metrics_chain(), build_rule_based_summary(), classify_ride_confidence_and_reason(), compute_ride_tss(), detect_intervals(), _normalized_power(), Compute Training Stress Score for a single ride.      TSS = (duration_s × NP²) / (+7 more)

### Community 64 - "Community 64"
Cohesion: 0.11
Nodes (18): classify_ride_purpose(), Estimate ride duration from a Strava-style time stream., Classify the overall purpose/category of a ride.      Categories (aligned with p, _stream_duration_seconds(), _constant_watts_stream(), End-to-end verification scenario for the AI Coach (Task 11).  This module tests, Verify each of the three synthetic rides gets the expected classification., 15-min easy ride at 50 % FTP → short_easy_spin. (+10 more)

### Community 65 - "Community 65"
Cohesion: 0.15
Nodes (11): AsyncClient, _build_metrics_chain_resilient(), _get_with_retry(), GET with one automatic retry after 16 s on HTTP 429., Fetch Strava activities and build the activity-metrics chain in the background., Return a safe Strava-streams mapping for analysis functions.      Strava can occ, Build metrics while tolerating failures on individual rides.      Returns ``(met, _run_import_background() (+3 more)

### Community 66 - "Community 66"
Cohesion: 0.20
Nodes (6): get_settings(), Centralised application configuration.  All environment variables are declared h, Return CORS origins parsed from the (possibly comma-separated) FRONTEND_URL., Return the public base URL for this server (used as Strava callback root)., Settings, BaseSettings

### Community 67 - "Community 67"
Cohesion: 0.22
Nodes (7): CandidateRow(), ConversationImportSettings(), formatCategory(), mockExtract, mockObserve, AthleteFactCandidate, extractAthleteFacts()

### Community 68 - "Community 68"
Cohesion: 0.18
Nodes (12): ActivitySource, fallback_fingerprint(), find_existing_import(), ImportedActivity, Source-neutral imported activity normalization.  The rest of the app still expos, Return a positive legacy BIGINT id for non-Strava source identifiers., synthetic_activity_id(), to_ride_inputs() (+4 more)

### Community 69 - "Community 69"
Cohesion: 0.18
Nodes (11): Build a compact structured-text block from a list of RideMetric ORM objects., ride_metrics_context_section(), For high-confidence rides the reason sub-line should not appear., If classification_confidence is absent the section should still render., test_ride_metrics_context_section_high_confidence_omits_reason_line(), test_ride_metrics_context_section_includes_duration_and_display_label(), test_ride_metrics_context_section_includes_matched_plan_snapshot(), test_ride_metrics_context_section_low_confidence_shows_reason() (+3 more)

### Community 71 - "Community 71"
Cohesion: 0.24
Nodes (6): RaceReadinessCard(), ReadinessContent(), statusLabel(), tsbLabel(), fetchReadinessScore(), ReadinessScore

### Community 72 - "Community 72"
Cohesion: 0.20
Nodes (10): adapt_plan_system(), hard_session_spacing_rules(), next_ride_recommendation_system(), adapt_plan_system must embed TRAINING_PLAN_PRINCIPLES like generate_plan_system, test_adapt_plan_system_includes_hard_session_spacing_rules(), test_adapt_plan_system_includes_training_principles(), test_adapt_plan_system_includes_tsb_guidance(), test_adapt_plan_system_preserves_hard_schedule_constraints() (+2 more)

### Community 73 - "Community 73"
Cohesion: 0.38
Nodes (6): _embed(), _make_openai(), Retrieval-Augmented Generation helper for cycling science.  Retrieves top-k rele, Return the embedding vector for *text_input* using OpenAI., Embed *query*, search knowledge_chunks by cosine similarity, and return     a (c, retrieve_cycling_context()

### Community 74 - "Community 74"
Cohesion: 0.33
Nodes (7): clear_athlete_memory(), get_coach_memory(), Return the CoachMemory for a user, or None., Create or update the CoachMemory for a user and flush., Delete all memory facts and clear coach memory text for a user., upsert_coach_memory(), CoachMemory

### Community 75 - "Community 75"
Cohesion: 0.33
Nodes (7): delete_intervals_token(), get_intervals_token(), Return the Intervals.icu token for a user, or None., Create or update Intervals.icu credentials for a user and flush., Delete Intervals.icu credentials for a user if present and flush., upsert_intervals_token(), IntervalsToken

### Community 76 - "Community 76"
Cohesion: 0.38
Nodes (4): EncryptedString, Transparently encrypts/decrypts string values using Fernet symmetric encryption., Fernet, TypeDecorator

### Community 77 - "Community 77"
Cohesion: 0.38
Nodes (6): _normalise_strava_sport_type(), Mirrors the TypeScript StravaActivity interface., StravaActivitySchema, _activity_date_for_analysis(), _activity_duration_for_analysis(), _dedupe_analysis_activities()

### Community 78 - "Community 78"
Cohesion: 0.38
Nodes (5): backendDir, dbFile, findUvicornCommand(), setup(), waitForBackend()

### Community 80 - "Community 80"
Cohesion: 0.33
Nodes (7): delete_strava_token(), get_strava_token(), Return the StravaToken for a user, or None., Create or update the StravaToken for a user and flush., Delete the StravaToken for a user if it exists and flush., upsert_strava_token(), StravaToken

### Community 81 - "Community 81"
Cohesion: 0.33
Nodes (4): mockAddChatMessage, mockAddPendingFeedbackRide, mockSaveChatMessage, mockSubmit

### Community 82 - "Community 82"
Cohesion: 0.33
Nodes (6): _auto_rate_ride(), Generate a coach note for a completed ride by comparing it against the plan., compare_planned_vs_actual(), Compare planned workout targets against actual Strava stream data.      Args:, Generate coach feedback for a matched ride and apply next-plan updates.      The, review_matched_ride_and_adapt()

### Community 83 - "Community 83"
Cohesion: 0.67
Nodes (5): _column_names(), _dedupe_source_external_rows(), downgrade(), _index_names(), upgrade()

### Community 84 - "Community 84"
Cohesion: 0.53
Nodes (4): _column_names(), _dedupe_source_external_rows(), _index_names(), upgrade()

### Community 86 - "Community 86"
Cohesion: 0.40
Nodes (5): AthleteMetricSnapshot, create_athlete_metric_snapshot(), get_athlete_metric_history(), Insert a new AthleteMetricSnapshot row and flush.      ``recorded_at`` defaults, Return the most recent *limit* AthleteMetricSnapshot rows for a user, oldest fir

### Community 87 - "Community 87"
Cohesion: 0.40
Nodes (5): create_chat_message(), get_chat_messages(), Return all ChatMessages for a user in persisted conversation order., Create a ChatMessage, flush, and return the persisted instance., ChatMessage

### Community 89 - "Community 89"
Cohesion: 0.50
Nodes (4): race_profile_context_section(), Return race context from the profile when a race was entered during setup., test_race_profile_context_empty_without_race_date_or_description(), test_race_profile_context_includes_race_date_and_description()

## Knowledge Gaps
- **200 isolated node(s):** `entrypoint.sh script`, `ai-trainer-backend`, `name`, `private`, `version` (+195 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ActivitySource` connect `Community 68` to `Community 27`?**
  _High betweenness centrality (0.242) - this node is a cross-community bridge._
- **What connects `Password hashing, JWT creation/verification, and FastAPI auth dependency.`, `Validate JWT_SECRET strength for the configured runtime environment.`, `Return a short-lived JWT that grants admin panel access.` to the rest of the system?**
  _774 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.010101010101010102 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.054612054612054615 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.04155374887082204 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.09371980676328502 - nodes in this community are weakly interconnected._
- **Should `Community 4` be split into smaller, more focused modules?**
  _Cohesion score 0.0544464609800363 - nodes in this community are weakly interconnected._