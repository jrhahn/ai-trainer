# Changelog

All notable changes to the frontend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-04-19

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
