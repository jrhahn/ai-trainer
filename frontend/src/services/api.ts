// Empty in production: the API is then called on the page's own origin, so
// every domain the app is served on works (ai-trainer-ops#45).
export const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined ?? 'http://localhost:8000').replace(/\/$/, '')

export const AUTHELIA_URL =
  ((import.meta.env.VITE_AUTHELIA_URL as string | undefined) ?? '').replace(/\/$/, '')

export const API_BASE = `${BACKEND_URL}/api/v1`
export const AUTH_EXPIRED_EVENT = 'ai-trainer:auth-expired'

/** Generate a short client-side correlation ID to include in every request. */
function generateRequestId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function getBrowserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

// "Stay signed in" (ai-trainer-ops#45). The web app's session lives in an
// HttpOnly cookie that no script can read. The store still holds an
// `authToken`, because 35 files pass it around and query keys include it, but
// for a cookie session it is only a marker: unique per sign-in, so one
// athlete's cached queries never answer for the next, and never sent as a
// bearer token. A real JWT (the integration tests use them) still is.
const SESSION_MARKER_PREFIX = 'cookie-session:'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export function newSessionMarker(): string {
  return `${SESSION_MARKER_PREFIX}${generateRequestId()}`
}

// In memory only: a reload gets it back from /auth/resume.
let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

/** Authorization and CSRF headers for a request, for the few raw `fetch` calls too. */
export function authHeaders(token: string | null | undefined, method = 'GET'): Record<string, string> {
  return {
    ...(token && !token.startsWith(SESSION_MARKER_PREFIX) ? { Authorization: `Bearer ${token}` } : {}),
    ...(csrfToken && !SAFE_METHODS.has(method.toUpperCase()) ? { 'X-CSRF-Token': csrfToken } : {}),
  }
}

export const UNREACHABLE_MESSAGE = 'Could not reach the server. Check your connection and try again.'
export const GATEWAY_MESSAGE =
  'The server took too long to answer. Wait a minute, then reload the page: what you started may have finished meanwhile.'
export const SERVER_ERROR_MESSAGE = 'Something went wrong on the server. Try again in a minute.'
const GATEWAY_STATUSES = new Set([502, 503, 504])

// How long a request may take before the athlete is told (ai-trainer-ops#267).
// Nothing had a limit: on a weak connection a page waited without end. What
// waits on a model, on intervals.icu or Strava, or on a whole account's data
// may take as long as the proxy gives it (nginx: 300 s), so the proxy's own
// answer still comes first there.
export const REQUEST_TIMEOUT_MS = 30_000
export const LONG_REQUEST_TIMEOUT_MS = 300_000
const LONG_PATHS = [
  '/ai/',
  '/intervals/',
  '/strava/',
  '/users/me/export',
  '/users/me/memory-export',
  '/users/me/recalculate-metrics',
  '/users/me/estimate-ftp',
  // Uploads: a phone on a slow line takes a while just to send the file
  // (ai-trainer-ops#276). A prefix, so it covers `/users/me/upload-fit/bulk`.
  '/users/me/upload-fit',
  '/tools/power-curve',
]

export function timeoutFor(path: string): number {
  return LONG_PATHS.some((prefix) => path.startsWith(prefix)) ? LONG_REQUEST_TIMEOUT_MS : REQUEST_TIMEOUT_MS
}
export const SLOW_MESSAGE = 'This is taking longer than usual. Check your connection and try again.'

interface ApiFetchOptions extends Omit<RequestInit, 'body'> {
  token?: string | null
  body?: unknown
  /** Instead of the default, ``timeoutFor(path)``. */
  timeoutMs?: number
}

// The interface language, sent so the backend has a reply language before the
// athlete has written anything (ai-trainer-ops#121). Set by App from
// useLanguage(); this module cannot read the store without an import cycle.
let interfaceLanguage: string | null = null

export function setInterfaceLanguage(language: string | null): void {
  interfaceLanguage = language
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { token, headers, body, timeoutMs, signal, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  // A timer and the caller's own signal on one controller: no AbortSignal.any,
  // which older Safari on a tester's phone does not have.
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs ?? timeoutFor(path))
  const forward = () => controller.abort()
  if (signal?.aborted) controller.abort()
  signal?.addEventListener('abort', forward, { once: true })
  try {
    const requestId = generateRequestId()
    const timezone = getBrowserTimezone()
    // A file upload goes as it is: the browser sets the multipart header and its
    // boundary (ai-trainer-ops#276).
    const form = typeof FormData !== 'undefined' && body instanceof FormData

    let response: Response
    try {
      response = await fetch(`${API_BASE}${path}`, {
        ...init,
        signal: controller.signal,
        credentials: init.credentials ?? 'include',
        headers: {
          ...(body !== undefined && !form ? { 'Content-Type': 'application/json' } : {}),
          ...authHeaders(token, method),
          // Asks every sign-in endpoint for a cookie session rather than a token.
          'X-Auth-Mode': 'cookie',
          'X-Request-ID': requestId,
          ...(timezone ? { 'X-App-Timezone': timezone } : {}),
          ...(interfaceLanguage ? { 'X-App-Language': interfaceLanguage } : {}),
          ...headers,
        },
        body: form ? body : body !== undefined ? JSON.stringify(body) : undefined,
      })
    } catch (e) {
      // A server that is down or a dropped connection threw "Failed to fetch",
      // which tells an athlete nothing (ai-trainer-ops#95). An abort is the
      // caller's own doing and stays what it is; the timer's is ours.
      if (timedOut) throw Object.assign(new Error(SLOW_MESSAGE), { status: 0, timedOut: true })
      if ((e as { name?: string } | null)?.name === 'AbortError') throw e
      throw Object.assign(new Error(UNREACHABLE_MESSAGE), { status: 0 })
    }

    // Prefer the server-echoed request ID so frontend + backend logs can be correlated.
    const serverRequestId = response.headers.get('X-Request-ID') ?? requestId

    if (!response.ok) {
      let message = 'Request failed'
      let parseFailReason: string | undefined
      try {
        const data = await response.json() as { detail?: string | Array<{ msg?: string }> }
        if (typeof data.detail === 'string') {
          message = data.detail
        } else if (Array.isArray(data.detail)) {
          message = data.detail.find((item) => item.msg)?.msg ?? message
          message = message.replace(/^Value error,\s*/i, '')
        }
      } catch (e) {
        if (timedOut) throw Object.assign(new Error(SLOW_MESSAGE), { status: 0, timedOut: true })
        if ((e as { name?: string } | null)?.name === 'AbortError') throw e
        parseFailReason = e instanceof Error ? e.message : 'non-JSON error body'
      }
      // No words from the backend: a proxy answered (nginx's 504 page when a model
      // call outlasts it) or the server crashed (ai-trainer-ops#95).
      if (message === 'Request failed' && response.status >= 500) {
        message = GATEWAY_STATUSES.has(response.status) ? GATEWAY_MESSAGE : SERVER_ERROR_MESSAGE
      }
      console.error('[api] Request failed', {
        method,
        path,
        status: response.status,
        contentType: response.headers.get('Content-Type'),
        requestId: serverRequestId,
        ...(parseFailReason !== undefined ? { parseFailReason } : {}),
      })
      // The admin page's token is its own session: an expired one ends that,
      // not the athlete's in the same browser (review of #1082).
      if (response.status === 401 && token && !path.startsWith('/admin/') && typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, {
          detail: { path, requestId: serverRequestId },
        }))
      }
      // The status rides along so a caller can tell an exhausted allowance (402)
      // from a failure (ai-trainer-ops#79); the message stays the server's words.
      throw Object.assign(new Error(message), { status: response.status })
    }

    if (response.status === 204) {
      return undefined as T
    }

    try {
      return await response.json() as T
    } catch (e) {
      // The body stalled until the timer stopped it, or the caller stopped
      // waiting: neither is a malformed answer (review of #1067).
      if (timedOut) throw Object.assign(new Error(SLOW_MESSAGE), { status: 0, timedOut: true })
      if ((e as { name?: string } | null)?.name === 'AbortError') throw e
      console.error('[api] Failed to parse successful response as JSON', {
        method,
        path,
        status: response.status,
        contentType: response.headers.get('Content-Type'),
        requestId: serverRequestId,
        parseError: e instanceof Error ? e.message : String(e),
      })
      throw new Error('Unexpected response format from server')
    }
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', forward)
  }
}
