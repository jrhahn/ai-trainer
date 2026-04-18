# Changelog

All notable changes to the frontend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-18

### Added

- **Strava connection error handling** — failures when initiating the Strava OAuth flow are now surfaced in two places:
  - **Browser console**: `console.error('[StravaConnect] Failed to get Strava auth URL: …')` logs the technical error message and the raw `Error` object (with stack trace) so developers can diagnose server-side misconfigurations (e.g. missing `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET`).
  - **UI**: an inline red error banner with an alert icon appears below the "Connect Strava" button: *"Could not start the Strava connection. Please try again."*
- **Strava callback error logging** — `StravaCallbackPage` now calls `console.error('[StravaCallback] Connection failed: …')` for both the `?error=…` query-param path (OAuth denial / backend error) and the missing-success-param fallback, making callback failures visible in the browser DevTools console alongside the existing user-facing error screen.
- **4 new Vitest tests** covering the error banner, console logging, and callback error logging (123 tests total).

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
