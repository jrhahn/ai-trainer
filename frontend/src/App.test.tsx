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
import { act, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { useAppStore } from './store/useAppStore'

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
  // The store is a module singleton, so one test's session would otherwise be
  // the next test's starting point — and the signed-out tests below would be
  // testing a signed-in app.
  useAppStore.setState({ authToken: null, isOnboarded: false, isLoadingUserData: false })
  window.history.pushState({}, '', '/')
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


describe('App, signed in but not yet told whose session this is (ai-trainer-ops#57)', () => {
  it('keeps the requested URL instead of answering before it can', () => {
    // A load that never settles, which is the whole window this is about: the
    // token is read synchronously at start-up but `isOnboarded` is not
    // persisted, so for as long as the profile is in flight the app holds a
    // session it knows nothing about.
    mockFetch.mockReturnValue(new Promise(() => {}))
    window.history.pushState({}, '', '/settings')
    useAppStore.setState({ authToken: 'tok-deep-link' })

    render(<App />)

    // Synchronously, with no `waitFor`: the redirect this prevents happened
    // *during* render, so a retrying assertion would simply wait past it and
    // then read the URL the bug had already replaced.
    expect(window.location.pathname).toBe('/settings')
  })

  it('does not strand the athlete there when the load fails', () => {
    // The gate has to lift on an answer of *either* kind. A failed profile load
    // still settles the question — the store signs the session out or raises
    // `dataLoadWarning` — and leaving it unresolved would hold a blank page for
    // as long as the backend stayed unreachable.
    mockFetch.mockRejectedValue(new Error('backend unreachable'))
    window.history.pushState({}, '', '/settings')
    useAppStore.setState({ authToken: 'tok-doomed' })

    render(<App />)

    return waitFor(() => {
      expect(window.location.pathname).not.toBe('/settings')
    })
  })

  it('holds the URL from the very first render after a sign-in, not one later', async () => {
    // The window a boolean would have left open: `authToken` goes null → value
    // while the "have we checked?" flag is still true, so that one render takes
    // the not-onboarded branch and replaces the URL before any effect can hold
    // it. Deriving the state from *which* token was checked closes it. Found in
    // review on PR #798.
    mockFetch.mockReturnValue(new Promise(() => {}))
    window.history.pushState({}, '', '/settings')

    render(<App />)
    // Signed out to begin with, so the landing page is correct here.
    expect(window.location.pathname).toBe('/')

    window.history.pushState({}, '', '/settings')
    await act(async () => {
      useAppStore.setState({ authToken: 'tok-fresh-login' })
    })

    // Still /settings: the sign-in must not cost the athlete the page they
    // asked for, which is also what a `from`-style redirect target depends on.
    expect(window.location.pathname).toBe('/settings')
  })

  it('does not blank the page after a logout that follows a completed load', async () => {
    // The case `!authToken ||` exists for, and the one a `checkedToken ===
    // authToken` comparison alone gets wrong: once a token *has* been looked up,
    // signing out leaves a stale token on one side of that comparison and `null`
    // on the other, so the gate would hold shut and render nothing to someone
    // who should be looking at the landing page.
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Headers(),
      json: async () => ({ detail: 'nope' }),
    })
    useAppStore.setState({ authToken: 'tok-looked-up' })
    window.history.pushState({}, '', '/settings')

    render(<App />)
    // Let the lookup finish, so the token counts as checked.
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      useAppStore.setState({ authToken: null })
    })

    expect(window.location.pathname).toBe('/')
  })

  it('does not blank the page for the render after a logout mid-load', async () => {
    // The other end of the same transition. With a boolean, `authToken` is null
    // while the flag is still false for one render, so the held branch would
    // render nothing to someone who should be looking at the landing page.
    mockFetch.mockReturnValue(new Promise(() => {}))
    useAppStore.setState({ authToken: 'tok-interrupted' })
    window.history.pushState({}, '', '/settings')

    render(<App />)
    expect(window.location.pathname).toBe('/settings')

    await act(async () => {
      useAppStore.setState({ authToken: null })
    })

    // Signed out is an answer, so the router may act on it immediately.
    expect(window.location.pathname).toBe('/')
  })

  it('still sends a visitor with no session to the landing page at once', () => {
    // The other side of the gate: with no token there is nothing to find out,
    // so it must not cost a visitor a blank frame.
    window.history.pushState({}, '', '/settings')

    render(<App />)

    expect(window.location.pathname).toBe('/')
  })
})
