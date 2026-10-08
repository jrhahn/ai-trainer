/**
 * One description of an end-to-end stack, so two of them cannot drift apart
 * (ai-trainer-ops#46).
 *
 * The suite needs the backend in two mutually exclusive states, and no process
 * can be in both:
 *
 * * **keyless** — no provider key and no fallback, which is the state a fresh
 *   deployment is in and the only way to reproduce the #41 dead end in a
 *   browser: plan generation answers 402;
 * * **stubbed** — `AI_STUB_PROVIDER=true`, so plan generation succeeds without
 *   a key and the dashboard can be reached at all.
 *
 * The stub is chosen *before* the key checks, so turning it on would silently
 * delete the first scenario. Hence two configs rather than two projects: a
 * project selects a browser and a viewport, not a server.
 *
 * Why a factory and not two files that each spell it out: everything except the
 * ports and four environment variables is identical, and the parts that are
 * identical are the parts that took measuring to get right — `--host 127.0.0.1`
 * because `vite preview` otherwise binds to `::1`, the raised registration cap,
 * one worker because the specs share a SQLite file. Two copies of that is two
 * places to fix the next thing we learn.
 */

import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test'

export interface StackOptions {
  /** Appears in report titles, so a failure says which stack produced it. */
  name: string
  testDir: string
  backendPort: number
  frontendPort: number
  /** The SQLite file, per stack: a shared one makes one suite the other's fixtures. */
  database: string
  /** What makes this stack different. Merged over the common environment. */
  backendEnv: Record<string, string>
}

export function defineStack(options: StackOptions): PlaywrightTestConfig {
  const outDir = `dist-${options.name}`
  const backendUrl = `http://127.0.0.1:${options.backendPort}`
  const frontendUrl = `http://127.0.0.1:${options.frontendPort}`

  const backendEnv: Record<string, string> = {
    DATABASE_URL: `sqlite+aiosqlite:///./${options.database}`,
    JWT_SECRET: 'e2e-secret-that-is-at-least-32-bytes-long',
    JWT_ALGORITHM: 'HS256',
    JWT_EXPIRE_MINUTES: '60',
    STRAVA_CLIENT_ID: 'e2e-strava-id',
    STRAVA_CLIENT_SECRET: 'e2e-strava-secret',
    FRONTEND_URL: frontendUrl,
    BACKEND_URL: backendUrl,
    APP_ENV: 'test',
    AUTHELIA_AUTH_ENABLED: 'false',
    // The global registration cap is 5 per hour (#753, consumed before the
    // existence check so it cannot be used to enumerate accounts). A handful of
    // specs across two viewports register more athletes than that in a minute,
    // so the real limit stops the suite — which it did, correctly. Raised for
    // the test backend only, rather than sharing one account between specs and
    // making each depend on the order of the others.
    REGISTRATION_RATE_LIMIT_ATTEMPTS: '100',
    ...options.backendEnv,
  }

  return defineConfig({
    name: options.name,
    testDir: options.testDir,
    // One worker: the specs register accounts in a shared SQLite file, and a
    // second worker would race the first one's rate limits rather than its rows.
    workers: 1,
    fullyParallel: false,
    forbidOnly: !!process.env.CI,
    retries: 0,
    reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
    timeout: 90_000,
    expect: { timeout: 15_000 },

    use: {
      baseURL: frontendUrl,
      // A distribution that does not ship Playwright's own Chromium can point at
      // its packaged one instead of chasing the browser revision Playwright
      // expects. Unset in CI, where `playwright install --with-deps` works and
      // the pinned revision is the one being tested.
      ...(process.env.PLAYWRIGHT_CHROMIUM_PATH
        ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } }
        : {}),
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
        command: `cd ../backend && uv run uvicorn main:app --host 127.0.0.1 --port ${options.backendPort}`,
        url: `${backendUrl}/healthz`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: backendEnv,
      },
      {
        // Built, not `vite dev`: the issue asks for the real build, and a dev
        // server differs in exactly the places that break in production —
        // module resolution, minification, and the env baked in at build time.
        //
        // `--host 127.0.0.1` is load-bearing. `vite preview` otherwise binds to
        // `localhost`, which resolves to ::1 here, so the IPv4 health check was
        // refused while the server was up and serving — and Playwright waited
        // out the full timeout and reported the frontend as never started.
        //
        // `VITE_BACKEND_URL` is baked in at build time, which is the whole
        // reason each stack needs a preview of its own rather than sharing one
        // — and why each needs its own `outDir`. Both used to build into
        // `dist/`, so two stacks running at once had the second build overwrite
        // the first: the keyless preview would then serve a bundle pointing at
        // the stubbed backend, and its 402 assertions would quietly stop
        // testing anything. CI never hit it because its steps are sequential,
        // which is the kind of latent trap that waits for the first person to
        // open two terminals. Raised in review.
        command:
          `npm run build -- --outDir ${outDir} && ` +
          `npm run preview -- --outDir ${outDir} --host 127.0.0.1 ` +
          `--port ${options.frontendPort} --strictPort`,
        url: frontendUrl,
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
        env: { VITE_BACKEND_URL: backendUrl },
      },
    ],
  })
}
