/**
 * End-to-end smoke test configuration (ai-trainer-ops#46).
 *
 * Two bugs that broke registration for *every* new athlete — onboarding
 * overwriting the name (#40) and the BYOK dead end (#41) — got through because
 * nothing tested the path a stranger takes. There are unit tests per page and a
 * contract suite per endpoint, and between them sat the only journey that
 * matters on day one.
 *
 * This starts a real backend and a real production build of the frontend, and
 * drives them in Chromium. Not jsdom: the two bugs were both about what the
 * browser ends up showing after a sequence of requests, which is the one thing
 * a component test cannot see.
 *
 * Ports and database are deliberately not the integration suite's. Both can run
 * at once locally, and a shared SQLite file would make one suite's leftovers the
 * other's fixtures.
 */

import { defineConfig, devices } from '@playwright/test'

const BACKEND_PORT = 18766
const FRONTEND_PORT = 4174

export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
export const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`

/** The backend environment. Mirrors the integration suite's, with the AI off.
 *
 * `ALLOW_ADMIN_AI_KEY_FALLBACK=false` is the interesting part and is set on
 * purpose: it is the mode the landing page promises ("you bring a Gemini key"),
 * and it makes plan generation answer 402 rather than call a provider. So the
 * suite needs no stub LLM and still exercises the dead end from #41 — the
 * failure an athlete actually meets on a fresh deployment.
 */
const backendEnv = {
  DATABASE_URL: 'sqlite+aiosqlite:///./e2e-test.db',
  JWT_SECRET: 'e2e-secret-that-is-at-least-32-bytes-long',
  JWT_ALGORITHM: 'HS256',
  JWT_EXPIRE_MINUTES: '60',
  STRAVA_CLIENT_ID: 'e2e-strava-id',
  STRAVA_CLIENT_SECRET: 'e2e-strava-secret',
  FRONTEND_URL,
  BACKEND_URL,
  APP_ENV: 'test',
  AUTHELIA_AUTH_ENABLED: 'false',
  ALLOW_ADMIN_AI_KEY_FALLBACK: 'false',
  GEMINI_API_KEY: '',
  OPENAI_API_KEY: '',
  // The global registration cap is 5 per hour (#753, consumed before the
  // existence check so it cannot be used to enumerate accounts). Six specs
  // across two viewports register six athletes in about a minute, so the real
  // limit stops the suite on its last test — which it did, and correctly.
  //
  // Raised here rather than worked around by sharing one account between specs:
  // a shared account makes every spec depend on the order of the others, and
  // the limit itself has its own backend tests. The deployed value is untouched.
  REGISTRATION_RATE_LIMIT_ATTEMPTS: '100',
}

export default defineConfig({
  testDir: './e2e',
  // One worker: the suite registers accounts in a shared SQLite file, and a
  // second worker would race the first one's rate limits rather than its rows.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  timeout: 90_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: FRONTEND_URL,
    // A distribution that does not ship Playwright's own Chromium can point at
    // its packaged one instead of chasing the browser revision Playwright
    // expects. Unset in CI, where `playwright install --with-deps` works and
    // the pinned revision is the one being tested.
    ...(process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } }
      : {}),
    // On failure only: a passing run should not cost CI an artifact upload, and
    // a failing one is unreadable without them.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },

  projects: [
    // Both widths the issue asks for. The same specs run twice, because a
    // layout that only works at one width is the defect class #51 collects.
    {
      name: 'desktop-1366',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 900 } },
    },
    {
      name: 'mobile-390',
      use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } },
    },
  ],

  webServer: [
    {
      command: 'cd ../backend && uv run uvicorn main:app --host 127.0.0.1 --port ' + BACKEND_PORT,
      url: `${BACKEND_URL}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: backendEnv,
    },
    {
      // Built, not `vite dev`: the issue asks for the real build, and a dev
      // server differs in exactly the places that break in production — module
      // resolution, minification, and the env baked in at build time.
      // `--host 127.0.0.1` is load-bearing, not tidiness. `vite preview`
      // otherwise binds to `localhost`, which resolves to ::1 on this box, so
      // the IPv4 health check below was refused while the server was up and
      // serving — Playwright then waited the full timeout and reported the
      // frontend as never having started.
      command:
        `npm run build && npm run preview -- --host 127.0.0.1 --port ${FRONTEND_PORT} --strictPort`,
      url: FRONTEND_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { VITE_BACKEND_URL: BACKEND_URL },
    },
  ],
})
