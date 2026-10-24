import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockFetch = vi.hoisted(() => vi.fn())
vi.stubGlobal('fetch', mockFetch)

import {
  GATEWAY_MESSAGE,
  LONG_REQUEST_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
  SERVER_ERROR_MESSAGE,
  SLOW_MESSAGE,
  timeoutFor,
  UNREACHABLE_MESSAGE,
  apiFetch,
  newSessionMarker,
  setCsrfToken,
  setInterfaceLanguage,
} from './api'

function makeResponse(
  status: number,
  body: unknown,
  extraHeaders: Record<string, string> = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    headers: new Headers({ 'Content-Type': 'application/json', ...extraHeaders }),
  } as unknown as Response
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('apiFetch', () => {
  it('returns parsed JSON on a successful GET request', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, { data: 'hello' }))

    const result = await apiFetch<{ data: string }>('/test')

    expect(result).toEqual({ data: 'hello' })
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/test',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Request-ID': expect.any(String) }),
      })
    )
  })

  it('includes Authorization header when token is provided', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))

    await apiFetch('/test', { token: 'my-token' })

    const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect((options.headers as Record<string, string>)['Authorization']).toBe('Bearer my-token')
  })

  it('includes Content-Type and body on POST request', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, { ok: true }))

    await apiFetch('/test', { method: 'POST', body: { name: 'Alice' } })

    const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect((options.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(options.body).toBe(JSON.stringify({ name: 'Alice' }))
  })

  it('returns undefined for 204 No Content responses', async () => {
    mockFetch.mockResolvedValue({
      ok: true, status: 204, json: vi.fn(), headers: new Headers(),
    } as unknown as Response)

    const result = await apiFetch<undefined>('/test', { method: 'DELETE' })

    expect(result).toBeUndefined()
  })

  it('throws with the detail message from a non-OK JSON response', async () => {
    mockFetch.mockResolvedValue(makeResponse(400, { detail: 'Invalid input' }))

    await expect(apiFetch('/test')).rejects.toThrow('Invalid input')
  })

  it('throws with the first validation detail from a non-OK JSON response', async () => {
    mockFetch.mockResolvedValue(makeResponse(422, {
      detail: [{ msg: 'Value error, Password must include a number.' }],
    }))

    await expect(apiFetch('/test')).rejects.toThrow('Password must include a number.')
  })

  it('includes cookies by default for Authelia-backed requests', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))

    await apiFetch('/test')

    const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(options.credentials).toBe('include')
  })

  it('says the server failed when a 500 brings no detail', async () => {
    mockFetch.mockResolvedValue(makeResponse(500, { message: 'Server error' }))

    await expect(apiFetch('/test')).rejects.toThrow(SERVER_ERROR_MESSAGE)
  })

  it('keeps the generic message for a client error without detail', async () => {
    mockFetch.mockResolvedValue(makeResponse(404, { message: 'nope' }))

    await expect(apiFetch('/test')).rejects.toThrow('Request failed')
  })

  // ai-trainer-ops#95: "Failed to fetch" told the athlete nothing.
  it('says the server could not be reached when fetch itself fails', async () => {
    mockFetch.mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(apiFetch('/test')).rejects.toMatchObject({ message: UNREACHABLE_MESSAGE, status: 0 })
  })

  it('says the server could not be reached when fetch rejects with no error at all', async () => {
    mockFetch.mockRejectedValue(null)

    await expect(apiFetch('/test')).rejects.toMatchObject({ message: UNREACHABLE_MESSAGE, status: 0 })
  })

  it('passes an abort through unchanged', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError')
    mockFetch.mockRejectedValue(abort)

    await expect(apiFetch('/test')).rejects.toBe(abort)
  })

  it('throws a generic message when the error response is not JSON', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.reject(new SyntaxError('not json')),
      headers: new Headers({ 'Content-Type': 'text/html' }),
    } as unknown as Response)

    await expect(apiFetch('/test')).rejects.toThrow(GATEWAY_MESSAGE)
  })

  it('sends an X-Request-ID header with every request', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))

    await apiFetch('/test')

    const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect((options.headers as Record<string, string>)['X-Request-ID']).toMatch(/^[0-9a-z]+-[0-9a-z]+$/)
  })

  it('sends the browser timezone with every request', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))

    await apiFetch('/test')

    const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect((options.headers as Record<string, string>)['X-App-Timezone']).toEqual(expect.any(String))
  })

  it('sends the interface language once App has set it (ai-trainer-ops#121)', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))

    setInterfaceLanguage('de')
    try {
      await apiFetch('/test')
      const [, options] = mockFetch.mock.calls[0] as [string, RequestInit]
      expect((options.headers as Record<string, string>)['X-App-Language']).toBe('de')
    } finally {
      setInterfaceLanguage(null)
    }
    await apiFetch('/test')
    const [, later] = mockFetch.mock.calls[1] as [string, RequestInit]
    expect((later.headers as Record<string, string>)['X-App-Language']).toBeUndefined()
  })

  it('logs structured metadata to console.error on a non-OK response', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockResolvedValue(makeResponse(500, { message: 'oops' }))

    await expect(apiFetch('/test', { method: 'GET' })).rejects.toThrow(SERVER_ERROR_MESSAGE)

    expect(consoleSpy).toHaveBeenCalledWith(
      '[api] Request failed',
      expect.objectContaining({ method: 'GET', path: '/test', status: 500 }),
    )
    consoleSpy.mockRestore()
  })

  it('dispatches an auth-expired event for authenticated 401 responses', async () => {
    const listener = vi.fn()
    window.addEventListener('ai-trainer:auth-expired', listener)
    mockFetch.mockResolvedValue(makeResponse(401, { detail: 'Invalid token' }))

    await expect(apiFetch('/test', { token: 'expired-token' })).rejects.toThrow('Invalid token')

    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener('ai-trainer:auth-expired', listener)
  })

  it('keeps the session when a step-up password is wrong (ai-trainer-ops#131)', async () => {
    // The server answers a mistyped confirmation password with 403: the session
    // is valid. Only a 401 may end it, or a typo signs the athlete out.
    const listener = vi.fn()
    window.addEventListener('ai-trainer:auth-expired', listener)
    mockFetch.mockResolvedValue(makeResponse(403, { detail: 'Incorrect password.' }))

    await expect(apiFetch('/auth/totp/disable', { token: 'live-token', method: 'POST' })).rejects.toMatchObject({
      message: 'Incorrect password.',
      status: 403,
    })

    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener('ai-trainer:auth-expired', listener)
  })

  it('includes parseFailReason in the error log when the error body is not JSON', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.reject(new SyntaxError('Unexpected token')),
      headers: new Headers({ 'Content-Type': 'text/html' }),
    } as unknown as Response)

    await expect(apiFetch('/test')).rejects.toThrow(GATEWAY_MESSAGE)

    expect(consoleSpy).toHaveBeenCalledWith(
      '[api] Request failed',
      expect.objectContaining({
        status: 503,
        parseFailReason: expect.stringContaining('Unexpected token'),
      }),
    )
    consoleSpy.mockRestore()
  })

  it('logs and throws when a 2xx response body is not valid JSON', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.reject(new SyntaxError('Unexpected token <')),
      headers: new Headers({ 'Content-Type': 'text/html' }),
    } as unknown as Response)

    await expect(apiFetch('/test')).rejects.toThrow('Unexpected response format from server')

    expect(consoleSpy).toHaveBeenCalledWith(
      '[api] Failed to parse successful response as JSON',
      expect.objectContaining({
        status: 200,
        parseError: expect.stringContaining('Unexpected token'),
      }),
    )
    consoleSpy.mockRestore()
  })
})

describe('cookie sessions (ai-trainer-ops#45)', () => {
  const headersOf = () =>
    (mockFetch.mock.calls[0] as [string, RequestInit])[1].headers as Record<string, string>

  beforeEach(() => {
    setCsrfToken(null)
    mockFetch.mockResolvedValue(makeResponse(200, {}))
  })

  it('never sends a session marker as a bearer token', async () => {
    await apiFetch('/test', { token: newSessionMarker() })

    expect(headersOf()).not.toHaveProperty('Authorization')
  })

  it('gives every sign-in its own marker, so cached queries do not cross sessions', () => {
    expect(newSessionMarker()).not.toBe(newSessionMarker())
  })

  it('asks for a cookie session on every request', async () => {
    await apiFetch('/auth/login', { method: 'POST', body: {} })

    expect(headersOf()['X-Auth-Mode']).toBe('cookie')
  })

  it('sends the CSRF token on a write', async () => {
    setCsrfToken('csrf-1')

    await apiFetch('/test', { method: 'POST', body: {}, token: newSessionMarker() })

    expect(headersOf()['X-CSRF-Token']).toBe('csrf-1')
  })

  it('leaves the CSRF token off a read', async () => {
    setCsrfToken('csrf-1')

    await apiFetch('/test', { token: newSessionMarker() })

    expect(headersOf()).not.toHaveProperty('X-CSRF-Token')
  })
})

describe('apiFetch timeouts (ai-trainer-ops#267)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  /** A server that never answers; the fetch rejects as a browser's does when its signal aborts. */
  function hangs() {
    mockFetch.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('The operation was aborted.', 'AbortError')),
          )
        }),
    )
  }

  it('says it is taking longer once a request has had 30 s', async () => {
    vi.useFakeTimers()
    hangs()
    const result = apiFetch('/users/me').catch((error) => error)

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)

    expect(await result).toMatchObject({ message: SLOW_MESSAGE, status: 0, timedOut: true })
  })

  it("gives a model call the proxy's own five minutes", async () => {
    vi.useFakeTimers()
    hangs()
    let settled = false
    const result = apiFetch('/ai/ask-trainer', { method: 'POST' }).catch((error) => {
      settled = true
      return error
    })

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS - REQUEST_TIMEOUT_MS)

    expect(await result).toMatchObject({ message: SLOW_MESSAGE, timedOut: true })
  })

  it.each([
    '/ai/ask-trainer',
    '/intervals/import-history',
    '/strava/activities',
    '/users/me/export',
    '/users/me/memory-export',
    '/users/me/recalculate-metrics',
    '/users/me/estimate-ftp',
  ])('gives %s the long limit, which waits on another service or a whole account', (path) => {
    expect(timeoutFor(path)).toBe(LONG_REQUEST_TIMEOUT_MS)
  })

  it.each(['/users/me', '/users/me/plan', '/auth/resume', '/users/me/chat'])('gives %s the 30 s limit', (path) => {
    expect(timeoutFor(path)).toBe(REQUEST_TIMEOUT_MS)
  })

  /** Headers that arrive, and a body that never does until the signal aborts. */
  function stallsInTheBody(status = 200) {
    mockFetch.mockImplementation((_url: string, init: RequestInit) =>
      Promise.resolve({
        ok: status < 300,
        status,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: () =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('The operation was aborted.', 'AbortError')),
            )
          }),
      }),
    )
  }

  it.each([200, 500])('times out a body that stalls after a %s, as the request does', async (status) => {
    vi.useFakeTimers()
    stallsInTheBody(status)
    const result = apiFetch('/users/me').catch((error) => error)

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)

    expect(await result).toMatchObject({ message: SLOW_MESSAGE, timedOut: true })
  })

  it('keeps a stop while the body is read an abort, not a malformed answer (review of #1067)', async () => {
    stallsInTheBody()
    const controller = new AbortController()
    const result = apiFetch('/users/me', { signal: controller.signal }).catch((error) => error)
    await Promise.resolve()

    controller.abort()

    expect(await result).toMatchObject({ name: 'AbortError' })
  })

  it("passes the caller's own abort through as an abort", async () => {
    hangs()
    const controller = new AbortController()
    const result = apiFetch('/users/me', { signal: controller.signal })

    controller.abort()

    await expect(result).rejects.toMatchObject({ name: 'AbortError' })
  })
})

