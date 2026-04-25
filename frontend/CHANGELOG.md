# Changelog

All notable changes to the frontend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.19.0] - 2026-04-25

### Added

- **`ReadinessExplainer` panel** (`src/components/RaceReadinessCard.tsx`) — a compact
  "How it works" info box rendered to the right of the readiness score on medium+ screens.
  Explains CTL, ATL, TSB, the scoring formula (form 65 % + fitness 35 %), the optimal TSB
  race-day window (+5 to +15), and the taper recommendation in concise bullet points.

- **"What to do next" recommendations** (`src/components/RaceReadinessCard.tsx`) — when the
  backend returns `recommendations`, they are displayed as purple bullet points below the
  metrics grid, giving the athlete specific, actionable next steps.

- **Periodic auto-refresh on `ExpertPage`** (`src/pages/ExpertPage.tsx`) — a `useEffect`
  with a 1-minute `setInterval` re-fetches `metricsHistory` and `rideMetricsHistory` from
  the backend and invalidates the `readiness-score` React Query cache. This keeps the
  Training Load chart, Athlete Progression chart, and Race Readiness card current as the
  date progresses without requiring a page reload.

### Changed

- **`RaceReadinessCard`** (`src/components/RaceReadinessCard.tsx`) — `useQuery` now uses
  `refetchInterval: 60 * 1000` (down from `staleTime: 5 * 60 * 1000`) so the readiness
  score auto-refreshes every minute independently. Layout switches to a responsive
  `flex-col md:flex-row` wrapper to accommodate the new explainer panel.

- **`ProgressionChart` — invalidates readiness query on recalculate**
  (`src/components/ProgressionChart.tsx`) — after `recalculateAll()` completes,
  `queryClient.invalidateQueries({ queryKey: ['readiness-score'] })` is called so the Race
  Readiness card immediately reflects the freshly recalculated metrics without waiting for
  the next poll cycle. `useQueryClient` import added.

- **`services/ai.ts`** — `ReadinessScore` and `BackendReadinessScore` interfaces gain a
  `recommendations: string[]` / `recommendations?: string[]` field; `fetchReadinessScore`
  maps it (defaulting to `[]` for backwards compatibility).

## [0.18.0] - 2026-04-24

### Added

- **`useMetricsPipeline` hook** (`src/hooks/useMetricsPipeline.ts`) — central orchestrator for
  all metric recalculation. Any profile change (FTP, HR zones) flows through this single hook
  rather than being scattered across call sites:
  - `updateMetrics(updates)` — persists profile changes via `PUT /users/me`, then calls
    `POST /users/me/recalculate-metrics` and refreshes both `metricsHistory` and
    `rideMetricsHistory` in the Zustand store in one atomic step.
  - `recalculateAll(ftpOverride?)` — triggers a full recalculation without changing the
    profile (used after Strava import and on manual recalculate requests).
  - Single shared `isPending` flag consumed by all call sites.

- **`src/utils/constants.ts`** — single source of truth for shared frontend constants.
  `THRESHOLD_HR_TO_MAX_HR_RATIO = 0.87` replaces five identical local declarations.

- **`src/utils/workout.ts`** — `parseLocalDate(dateStr)` helper that appends `T12:00:00` to
  date strings before constructing a `Date`, preventing timezone off-by-one errors. Replaces
  inline copies in `TrainingCalendar`, `WorkoutCard`, and `WorkoutPage`.

- **`src/components/charts/LineChart.tsx`** — shared SVG line-chart component extracted from
  the two identical private implementations that existed in `ProgressionChart` and
  `TrainingLoadChart`. Supports up to three series (solid, dashed, dotted) and an optional
  zero-reference line.

### Changed

- **`FitnessMetricsCard`** (`src/components/FitnessMetricsCard.tsx`) — `save()` now routes
  through `useMetricsPipeline.updateMetrics()` instead of calling `updateCurrentUser` and
  `setUserProfile` directly. The local `LTHR_RATIO` constant is removed in favour of
  `THRESHOLD_HR_TO_MAX_HR_RATIO` from `utils/constants`.

- **`SettingsPage`** (`src/pages/SettingsPage.tsx`) — `handleRecalculate` and
  `handleConfirmFTP` both use the pipeline. `handleSaveHR` now explicitly persists HR values
  to the backend via `PUT /users/me` *before* calling `POST /users/me/estimate-ftp`, closing
  a gap where HR updates were sent to the estimate endpoint but not saved to the profile.

- **`useStravaSync`** (`src/hooks/useStravaSync.ts`) — calls `recalculateAll()` after every
  Strava analysis run so that all historical rides are kept in sync with the latest profile
  metrics without requiring a manual recalculate step.

- **`ProgressionChart`** (`src/components/ProgressionChart.tsx`) — private `LineChart`
  function removed; now imports the shared component from `components/charts/LineChart`.
  `handleRecalculate` uses `useMetricsPipeline.recalculateAll()` instead of calling
  `recalculateMetrics` + `fetchMetricsHistory` + `setMetricsHistory` inline.

- **`TrainingLoadChart`** (`src/components/TrainingLoadChart.tsx`) — private `LineChart`
  function removed; now imports the shared component from `components/charts/LineChart`.

- **`RaceReadinessCard`** (`src/components/RaceReadinessCard.tsx`) — replaced unnecessary
  `useShallow` wrapper (single-field selector) with a plain `useAppStore` selector.

- **`StravaCallbackPage`** (`src/pages/StravaCallbackPage.tsx`) — replaced inline `apiFetch`
  call with `triggerStravaHistoryImport` from `services/strava`.

- **`useImportProgress`** (`src/hooks/useImportProgress.ts`) — removed duplicate local
  `ImportProgress` interface; re-exports the canonical type from `services/strava`.

### Removed

- Dead `saveChatMessage` function from `src/services/user.ts` was **restored** — it was
  removed in error; `POST /users/me/chat` is still exercised by integration tests.

## [0.17.0] - 2026-04-22

### Added

- **`TrainingLoadChart` component** (`src/components/TrainingLoadChart.tsx`) — new expert-mode
  time-series chart that shows how training load metrics develop ride-by-ride using the
  granular per-ride data from the backend (one data point per ride):
  - **CTL** (Fitness) — 42-day exponential weighted average, blue line chart
  - **ATL** (Fatigue) — 7-day exponential weighted average, orange line chart
  - **TSB** (Form) — CTL minus ATL with a dashed zero reference line so positive/negative
    form is immediately visible; green line chart
  - **Daily TSS** — training stress score per ride, purple line chart
  - Summary badges showing the latest CTL, ATL, TSB, and most recent TSS
  - Empty-state card when no ride data has been synced yet

- **`TrainingLoadChart` on `ExpertPage`** (`src/pages/ExpertPage.tsx`) — rendered below the
  existing `ProgressionChart` so expert users can see both the high-level FTP progression
  (from sparse analysis snapshots) and the detailed per-ride CTL/ATL/TSB time series.

- **`RideMetricPoint` interface** exported from `src/store/useAppStore.ts` — camelCase
  representation of a per-ride metric row: `activityDate`, `sportType`, `tss`, `ctlAfter`,
  `atlAfter`, `tsbAfter`, `durationSeconds`, `avgPowerW`, `normalizedPowerW`.

- **`rideMetricsHistory` state** and `setRideMetricsHistory` action added to Zustand store
  (`useAppStore.ts`).

- **`fetchRideMetricsHistory(authToken)`** added to `src/services/user.ts` — calls
  `GET /users/me/ride-metrics-history` and returns a typed `RideMetricPoint[]`.

- **`loadUserData`** updated to fetch ride metrics history in parallel with all other data
  on app startup (no extra round-trips).

- **Tests** (`src/store/useAppStore.test.ts`) — updated mock for `../services/user` to
  include `fetchRideMetricsHistory`, and extended the `loadUserData` hydration test to
  assert that `rideMetricsHistory` is correctly stored in the Zustand store.


### Added

- **Heart Rate Settings section in Settings** (`src/pages/SettingsPage.tsx`) — a new card that
  lets users update Max HR and Resting HR at any time, with a two-step FTP re-estimation
  workflow:

  1. **Save & Estimate FTP** — persists the entered HR values via
     `POST /users/me/estimate-ftp`.  If an FTP estimate is available it is shown in an inline
     confirmation panel.
  2. **Confirm & Recalculate** — the user can review or adjust the estimated FTP, then click
     this button to trigger a full `POST /users/me/recalculate-metrics` rebuild of all
     TSS / CTL / ATL / TSB values for every stored ride.  A `window.confirm` guard and an
     irreversibility warning banner are shown before any data is changed.

  An *age* field is provided as a fallback when the athlete doesn't know their Max HR — the
  app estimates it as `220 − age` (minimum age 10 to avoid unrealistic values) and shows a
  live preview.

- **`estimateFTP()` API helper** (`src/services/user.ts`) — thin wrapper around
  `POST /users/me/estimate-ftp` that accepts optional `maxHeartRate` and `restingHeartRate`
  and returns `{ estimatedFTP, source }`.

- **Onboarding — Max HR and Resting HR collected before Strava OAuth** (`src/pages/OnboardingPage.tsx`):

  - In **Step 3** (Strava flow) Max HR + Resting HR inputs now appear *before* the Strava
    Connect button so the values are captured before the OAuth redirect.
  - An **age** field is shown when Max HR is blank.  The estimated Max HR (`220 − age`,
    min age 10) is computed live and used transparently during onboarding — no manual
    calculation needed.
  - **Resting HR defaults to 60** at plan-generation time when the field is left blank,
    removing a common source of missing data.

- **Tests** (`src/services/user.test.ts`, `src/pages/SettingsPage.test.tsx`,
  `src/pages/OnboardingPage.test.tsx`):
  - `user.test.ts`: two new describe blocks for `recalculateMetrics()` (no-override and
    with-override cases) and `estimateFTP()` (full HR, empty opts, max-only).
  - `SettingsPage.test.tsx`: six new tests covering the Heart Rate Settings card — button
    disabled state, `estimateFTP` call arguments, FTP confirmation panel lifecycle, and
    `recalculateMetrics` invocation after confirmation.
  - `OnboardingPage.test.tsx`: two new tests — age-based Max HR derivation (220 − 35 = 185)
    and default resting HR = 60 when the field is left blank.



### Added

- **FTP Management section in Settings** (`src/pages/SettingsPage.tsx`) — a new card between
  Account and Strava Integration exposes three actions:

  1. **Save FTP** — override `current_ftp` on the user profile with a manually entered value.
  2. **Recalculate TSS / ATL / CTL** — calls the new `POST /users/me/recalculate-metrics` backend
     endpoint with an optional FTP value, rebuilding the full training-stress chain for every
     stored ride.  A warning banner explains the operation is irreversible, and a `window.confirm`
     dialog is shown before any data is sent.
  3. Displays the current stored FTP next to the card heading for quick reference.

- **`recalculateMetrics()` API helper** (`src/services/user.ts`) — thin wrapper around
  `POST /users/me/recalculate-metrics` that returns `{ updated, ftpUsed }`.

### Changed

- **Strava callback — automatic ride history import** (`src/pages/StravaCallbackPage.tsx`) — after
  a successful OAuth connection the page now calls `POST /strava/import-history` before redirecting
  to the dashboard. The user sees a three-step progress indicator: *Connecting → Importing ride
  history (last 6 months) → All set! N rides imported*. Import errors are non-fatal; the user
  is redirected to the dashboard regardless.

## [0.14.0] - 2026-04-22

### Added

- **Markdown rendering in coach chat** (`src/components/AIChat.tsx`) — assistant messages are now
  rendered with `react-markdown`. Inline formatting such as **bold** (`**text**`) and *italic*
  (`*text*`) is displayed correctly. Headings (`#`, `##`, …) are remapped to plain paragraphs so
  the chat stays at a uniform font size throughout.

### Changed

- **Multi-line chat input** (`src/components/AIChat.tsx`) — the single-line `<input type="text">`
  has been replaced with an auto-growing `<textarea>`. Pressing **Shift+Enter** inserts a new line;
  pressing **Enter** alone (or clicking the send button) submits the message as before.

## [0.12.0] - 2026-04-20

### Changed

- **Coach welcome message** (`src/components/AIChat.tsx`) — opening message updated to match the
  new friendly coach persona: casual greeting with emoji, framing the coach as a knowledgeable
  friend rather than a formal AI system.

## [0.11.0] - 2026-04-20

### Added

- **Post-login ride summary card on Dashboard** (`src/pages/DashboardPage.tsx`) — a blue-tinted summary card is rendered below the greeting when `riderAssessment.loginSummary` is available. It displays the AI coach's structured 4-part analysis (what the athlete did, FTP insights, plan alignment, and training conclusions) so athletes get immediate context every time they open the app after a ride.

- **`loginSummary` field in `RiderAssessment` interface** (`src/store/useAppStore.ts`) — mirrors the new backend field; populated automatically when `analyseStravaActivities` completes.

## [0.10.0] - 2026-04-20

### Added

- **`ProgressionChart` on the default Dashboard** (`src/pages/DashboardPage.tsx`) — rendered below the AI chat panel so athletes can track FTP / CTL / ATL / TSB history without navigating to the Expert page; `metricsHistory` is already loaded on login so no extra fetch is required

### Changed

- **`ProgressionChart` empty-state** (`src/components/ProgressionChart.tsx`) — instead of rendering `null` when fewer than 2 metric snapshots exist, the component now shows a styled card with the message *"Come back after your next analysis to see your fitness progression chart."* This improves the first-run experience for new users



### Changed

- **"Next 3 days" always shows upcoming sessions** — `DashboardPage` now filters `trainingPlan` to only include days ≥ today, so the strip is never filled with past workouts.
- **Stale-plan auto-adaptation** — when the training plan contains past incomplete sessions, `DashboardPage` automatically calls the `/ai/adapt-plan` endpoint so the AI coach can reschedule those sessions to upcoming dates. A subtle "Updating plan with your coach…" label is shown while the request is in flight.
- **Chat messages inverted — latest on top** — `AIChat` now renders messages in reverse chronological order (newest at the top) using `flex-col-reverse`, eliminating the need to scroll down to see the latest exchange.

## [0.8.0] - 2026-04-19

### Added

- **`ExpertPage`** (`src/pages/ExpertPage.tsx`) — new `/expert` route containing all data-dense components moved out of the default dashboard:
  - `TrainingCalendar` full calendar view
  - `FitnessMetricsCard` (FTP, HR zones, edit)
  - `ProgressionChart` (FTP / CTL / ATL / TSB history)
  - `RaceReadinessCard` (shown when training for a race)
  - `FitFileUpload` (.fit import)
  - `StravaConnect` + all Strava analysis status banners (detecting, analysing, done, error)
  - Last-ride feedback and ride-insights details
  - Recent Strava activities list
- **Expert nav link** added to `Layout` sidebar (`FlaskConical` icon, `/expert` route); Coach link renamed from "Dashboard"
- **`compact` prop** on `WorkoutCard` — renders a single-line row (`date · type badge · title · duration`) suitable for the 3-day strip; clicking still navigates to the workout detail page
- **`className` prop** on `AIChat` — lets the parent control the component's height and shadow; falls back to the previous fixed `h-[28rem] shadow-sm` when omitted

### Changed

- **`DashboardPage` redesigned as minimal Coach view** — all heavy components removed; now shows:
  1. Greeting header with today's date
  2. "Next 3 days" strip using the new compact `WorkoutCard` variant
  3. "Ask your coach" chat panel that fills the remaining viewport height (`calc(100vh - 22rem)`)
- **`WorkoutCard` badge palette** simplified to monochrome gray (`bg-gray-100 / bg-gray-900`) instead of the previous per-type colour set, reducing visual noise



### Added

- **`FitFileUpload` component** (`src/components/FitFileUpload.tsx`) — dashboard card for importing workouts directly from `.fit` files without a Strava account:
  - File input restricted to `.fit` extension; validated client-side before upload
  - Sends file to `POST /users/me/upload-fit` via raw `fetch` with `FormData`
  - Shows parsed sport type, duration, average power, and average heart rate on success
  - Error state with human-readable server error message
  - Supports Garmin, Wahoo, and Zwift exports equally
- **`uploadFitFile(token, file)`** added to `src/services/user.ts` — posts to `/users/me/upload-fit` and returns typed summary object
- **`FitFileUpload` wired into `DashboardPage`** — rendered below `RaceReadinessCard` and above `StravaConnect`

## [0.6.0] - 2026-04-19

### Added

- **`ProgressionChart` component** (`src/components/ProgressionChart.tsx`) — SVG-based dashboard chart showing the athlete's performance progression over time:
  - FTP history line chart (watts)
  - Threshold HR history line chart (bpm)
  - CTL / ATL / TSB (fitness / fatigue / form) overlaid trend lines
  - Summary badges showing latest FTP, CTL, ATL, and TSB (TSB badge coloured green/red by sign)
  - Renders only when ≥ 2 data points exist; no added frontend dependencies
- **`metricsHistory` state** and `setMetricsHistory` action added to Zustand store (`useAppStore.ts`)
- **`AthleteMetricSnapshot` interface** exported from `useAppStore.ts`
- **`fetchMetricsHistory(authToken)`** added to `src/services/user.ts` — calls `GET /users/me/metrics-history` and returns typed snapshot list
- **`loadUserData`** updated to fetch metrics history in parallel with other data on app startup
- **`ProgressionChart` wired into `DashboardPage`** — rendered below `FitnessMetricsCard`

## [0.5.0] - 2026-04-19

### Added

- **`RaceReadinessCard` component** (`src/components/RaceReadinessCard.tsx`) — dashboard card that fetches and displays the race-day readiness score:
  - Animated SVG score ring colour-coded by score range (green ≥ 75 / amber ≥ 50 / orange ≥ 25 / red below)
  - Status label (`Peak Form`, `Race Ready`, `Building`, `Fatigued`, `Rest Needed`)
  - Days-to-race countdown badge and race-day indicator
  - CTL / ATL / TSB / form-score metrics grid
  - Race-day projection section (projected score + CTL/TSB at race day) when `raceDate` is set and lies in the future
  - Data fetched via `@tanstack/react-query` with a 5-minute stale time; handles loading and error states
- **`fetchReadinessScore(authToken)`** added to `src/services/ai.ts` — calls `GET /ai/readiness-score` and maps the snake_case response to the camelCase `ReadinessScore` interface
- **`ReadinessScore` interface** exported from `src/services/ai.ts`
- **`RaceReadinessCard` wired into `DashboardPage`** — rendered below `FitnessMetricsCard` when the user's `trainingGoal === 'race'` or `raceDate` is set

## [0.4.0] - 2026-04-18

### Added

- **Structured workout coaching detail** — `WorkoutPage` now displays three sections below the title/badges:
  - **Session description** — the existing `description` field, now generated with exact power/HR numbers (e.g. "Ride 90 min at 195–220 W (Zone 2, 75–85% of your 260 W FTP). Keep HR under 148 bpm.")
  - **"Why this workout"** (amber card) — `workoutPurpose`: 1–2 sentences explaining the physiological goal and placement in the plan (e.g. "This tempo block raises your lactate threshold by training your body to clear lactate more efficiently.")
  - **"Key Focus Points"** (blue card, bulleted list) — `keyFocusPoints`: 3–5 action-verb coaching cues (e.g. "Keep cadence between 88–95 rpm throughout", "HR must stay below 158 bpm (Zone 3); back off if it creeps higher")
- **"Regenerate plan" nudge** — when `workoutPurpose` is absent (plans generated before this release), a notice is shown on the workout card prompting the athlete to regenerate their plan to unlock coaching cues
- `workoutPurpose?: string` and `keyFocusPoints?: string[]` optional fields added to the `TrainingDay` interface in `useAppStore.ts`
- `workoutPurpose` and `keyFocusPoints` added to the `PlanDayUpdate` interface in `services/ai.ts` so coach-chat plan updates also carry coaching detail

## [0.3.0] - 2026-04-18

### Added

- **Frontend/backend integration test suite** (`src/test/integration/api-contract.test.ts`) — 24 tests that call the real TypeScript service functions (no `vi.mock()`) against a live SQLite-backed FastAPI process:
  - Covers all non-AI, non-Strava endpoints: `POST /auth/register`, `POST /auth/login`, `GET/PUT /users/me`, `DELETE /users/me`, `GET/PUT /users/me/plan`, `GET /users/me/workouts`, `POST /users/me/workouts/{date}`, `GET/POST/DELETE /users/me/chat`, `GET/PUT /users/me/coach-memory`
  - `src/test/integration/global-setup.ts` — spawns `uvicorn` before the suite, polls `/healthz`, tears down and removes the SQLite database after the run
  - `vitest.integration.config.ts` — separate Vitest config (`environment: 'node'`, port `18765`) so unit tests are unaffected
- `test:integration` npm script
- CI `integration` job (Node 20 + Python 3.12 + uv) runs `npm run test:integration` on every push
- **Strava connection error handling** — failures when initiating the Strava OAuth flow are now surfaced in two places:
  - **Browser console**: `console.error('[StravaConnect] Failed to get Strava auth URL: …')` logs the technical error message and the raw `Error` object (with stack trace) so developers can diagnose server-side misconfigurations (e.g. missing `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET`).
  - **UI**: an inline red error banner with an alert icon appears below the "Connect Strava" button: *"Could not start the Strava connection. Please try again."*
- **Strava callback error logging** — `StravaCallbackPage` now calls `console.error('[StravaCallback] Connection failed: …')` for both the `?error=…` query-param path (OAuth denial / backend error) and the missing-success-param fallback, making callback failures visible in the browser DevTools console alongside the existing user-facing error screen.
- **4 new Vitest tests** covering the error banner, console logging, and callback error logging (127 tests total).

## [0.2.0] - 2026-04-18

### Changed

- `askTrainer` now sends only `{ question, contextWorkout? }` — plan, profile, rider assessment, coach memory, and conversation history are no longer included in the request body; the backend loads these from the database
- `adaptTrainingPlan` now sends only `{ recentFeedback }` — plan and profile removed from request body
- `generateTrainingPlan` now sends an empty body — profile and rider assessment removed from request body
- `rateCompletedWorkout` no longer sends `profile` in the request body
- `AIChat` component no longer orchestrates `saveChatMessage`, `saveTrainingPlan`, `updateCoachMemory`, or `saveCoachMemoryRemote` calls after each exchange — all side-effects are handled server-side inside `/ai/ask-trainer`
- After `askTrainer` resolves, coach memory is re-fetched from the server to stay in sync with the backend-updated value

### Added

- `setChatHistory` action added to `useAppStore` for overwriting the full chat history from a server response

### Removed

- `updateCoachMemory` exported function removed from `services/ai.ts` (endpoint removed server-side)

## [0.1.0] - 2026-04-14

### Added

- AI-powered cycling training plan generation and adaptation via OpenAI or Google Gemini
- AI coach chat with persistent memory and per-session conversation history
- Strava integration: connect account, fetch activities, and analyse rides to personalise the training plan
- Workout logging with perceived effort, duration, and notes; post-workout AI coach feedback
- Multi-step onboarding flow supporting both manual fitness entry and Strava-based assessment
- Fitness metrics widget (FTP, heart rate zones) with live calendar updates
- `contextWorkout` support: AI chat scoped to today's workout when opened from the workout page
- Training plan calendar with day-level detail and coach feedback badges
- Multi-provider AI support: OpenAI (`gpt-4o-mini`) and Google Gemini (`gemini-2.0-flash`), switchable from Settings
- Settings page for profile management, AI provider selection, and Strava disconnect
- Auth: email/password login and registration; Authelia SSO when `VITE_AUTHELIA_URL` is configured
- JWT token persisted in `sessionStorage` to survive Strava OAuth redirects
- TanStack Query (v5) for async state: activities, incremental Strava polling, mutations
- `useStravaSync` hook extracts Strava polling and analysis logic from DashboardPage
- Zustand store with `useShallow` selectors to prevent unnecessary re-renders
- Vitest test suite with 119 tests and Codecov integration
- GitHub Actions CI workflow (lint → test → coverage upload)
