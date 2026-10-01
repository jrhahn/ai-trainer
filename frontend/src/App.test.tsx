/**
 * What a logged-out visitor costs (#704).
 *
 * App used to probe `GET /auth/session` on every logged-out page load, behind an
 * overlay reading "Checking your secure session… Authelia will continue sign-in
 * if needed." It could never succeed — that endpoint answers from `Remote-*`
 * headers and forward-auth is attached to no router (#696) — so every visitor
 * paid a round trip and a flash of Authelia before the landing page, and the
 * backend logged a warning for each one.
 *
 * It was reported as "I still briefly see Authelia before the homepage". These
 * tests exist because the probe had none, which is how it survived being
 * pointless for as long as it did.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from './App'

const mockFetch = vi.fn()

// AUTHELIA_URL is empty in the test environment, so without this the probe
// would not have fired here either and these tests would pass against the very
// code they exist to rule out. `apiFetch` stays real: a probe has to reach the
// stubbed `fetch` for its absence to mean anything.
vi.mock('./services/api', async () => ({
  ...(await vi.importActual<typeof import('./services/api')>('./services/api')),
  AUTHELIA_URL: 'https://auth.trainlikea.pro',
}))

vi.mock('./hooks/useImportProgress', () => ({
  useImportProgress: () => ({
    status: 'idle',
    total: 0,
    processed: 0,
    imported: 0,
    skipped: 0,
    failedActivities: [],
    error: '',
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  mockFetch.mockResolvedValue({
    ok: false,
    status: 401,
    headers: new Headers(),
    json: async () => ({ detail: 'Authelia session not found' }),
  })
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App, signed out', () => {
  it('reaches the landing page without asking the backend anything', async () => {
    render(<App />)

    await waitFor(() => {
      expect(screen.queryByText(/Checking your secure session/i)).not.toBeInTheDocument()
    })
    // Not "does not call /auth/session" but "calls nothing": a logged-out
    // visitor has no session to check, and any request here is a round trip
    // between them and the page.
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('never mentions Authelia to a visitor who has no session there', () => {
    render(<App />)

    // Checked on the first paint, deliberately without `waitFor`. The overlay
    // used to appear immediately and disappear once the probe failed, so a
    // retrying assertion passes against the broken code — it just waits out the
    // flash this test is about.
    expect(screen.queryByText(/Authelia/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Checking your secure session/i)).not.toBeInTheDocument()
  })
})
