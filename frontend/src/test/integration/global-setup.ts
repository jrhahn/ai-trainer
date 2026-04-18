/**
 * Vitest global setup for integration tests.
 *
 * Spawns the FastAPI backend on INTEGRATION_BACKEND_PORT using `uv run uvicorn`,
 * waits for the /healthz endpoint to respond, then tears the process down after
 * all integration tests complete.
 *
 * The backend is configured with:
 *  - SQLite (integration-test.db) so no PostgreSQL is required
 *  - Fake API keys — AI endpoints are not exercised in integration tests
 *  - AUTHELIA_AUTH_ENABLED=false so standard JWT auth applies
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { unlink } from 'node:fs/promises'
import { homedir } from 'node:os'
import { resolve, join } from 'node:path'

export const INTEGRATION_BACKEND_PORT = 18765

// process.cwd() is the frontend/ directory when vitest runs via `npm run test:integration`.
// The backend/ directory is one level up from there.
const backendDir = resolve(process.cwd(), '../backend')
const dbFile = join(backendDir, 'integration-test.db')

let backendProcess: ChildProcess | null = null

/** Build the command + args for starting uvicorn.
 *
 * Preference order:
 * 1. backend/.venv/bin/uvicorn (already set up by `uv sync`)
 * 2. `uv run uvicorn` (needs uv on PATH; used in CI before a venv is present)
 */
function findUvicornCommand(): { cmd: string; args: string[] } {
  const venvUvicorn = join(backendDir, '.venv', 'bin', 'uvicorn')
  const appArgs = ['main:app', '--host', '127.0.0.1', '--port', String(INTEGRATION_BACKEND_PORT)]
  if (existsSync(venvUvicorn)) {
    return { cmd: venvUvicorn, args: appArgs }
  }

  // Fall back to `uv run uvicorn` — try common install paths
  const uvCandidates = [
    process.env.UV_PATH,
    join(homedir(), '.local', 'bin', 'uv'),
    '/usr/local/bin/uv',
    'uv',
  ]
  const uvBin = uvCandidates.find(c => c && (c === 'uv' || existsSync(c))) ?? 'uv'
  return { cmd: uvBin as string, args: ['run', 'uvicorn', ...appArgs] }
}

async function waitForBackend(maxAttempts = 40): Promise<void> {
  const url = `http://127.0.0.1:${INTEGRATION_BACKEND_PORT}/healthz`
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await fetch(url)
      if (res.ok) return
    } catch {
      // not ready yet
    }
    await new Promise(r => setTimeout(r, 500))
  }
  throw new Error(
    `Integration backend did not start within ${maxAttempts * 0.5}s. ` +
    'Make sure `uv` is installed and backend dependencies are synced (`uv sync --group dev` in backend/).'
  )
}

export async function setup(): Promise<void> {
  // Remove a stale DB from a previous interrupted run
  if (existsSync(dbFile)) {
    await unlink(dbFile)
  }

  const { cmd, args } = findUvicornCommand()

  // Set PYTHONPATH so that uvicorn can import the backend app modules
  const augmentedPath = [
    join(backendDir, '.venv', 'bin'),
    process.env.PATH ?? '',
  ].join(':')

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PATH: augmentedPath,
    DATABASE_URL: 'sqlite+aiosqlite:///./integration-test.db',
    JWT_SECRET: 'integration-test-secret-that-is-at-least-32-characters-long',
    JWT_ALGORITHM: 'HS256',
    JWT_EXPIRE_MINUTES: '60',
    STRAVA_CLIENT_ID: 'test-strava-id',
    STRAVA_CLIENT_SECRET: 'test-strava-secret',
    FRONTEND_URL: 'http://localhost:5173',
    BACKEND_URL: `http://127.0.0.1:${INTEGRATION_BACKEND_PORT}`,
    OPENAI_API_KEY: 'test-openai-key',
    GEMINI_API_KEY: 'test-gemini-key',
    APP_ENV: 'test',
    AUTHELIA_AUTH_ENABLED: 'false',
  }

  backendProcess = spawn(
    cmd,
    args,
    { cwd: backendDir, env, stdio: 'pipe' },
  )

  backendProcess.on('error', (err: Error) => {
    console.error('[integration-backend] spawn error:', err.message)
    console.error(`[integration-backend] command: ${cmd} ${args.join(' ')}`)
  })

  await waitForBackend()
}

export async function teardown(): Promise<void> {
  if (backendProcess) {
    backendProcess.kill('SIGTERM')
    await new Promise<void>(resolve => {
      const timer = setTimeout(resolve, 3000)
      backendProcess!.on('exit', () => {
        clearTimeout(timer)
        resolve()
      })
    })
    backendProcess = null
  }

  try {
    await unlink(dbFile)
  } catch {
    // file may have already been removed
  }
}

