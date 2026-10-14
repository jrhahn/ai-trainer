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

interface ApiFetchOptions extends Omit<RequestInit, 'body'> {
  token?: string | null
  body?: unknown
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { token, headers, body, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  const requestId = generateRequestId()
  const timezone = getBrowserTimezone()

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: init.credentials ?? 'include',
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(token, method),
        // Asks every sign-in endpoint for a cookie session rather than a token.
        'X-Auth-Mode': 'cookie',
        'X-Request-ID': requestId,
        ...(timezone ? { 'X-App-Timezone': timezone } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    // A server that is down or a dropped connection threw "Failed to fetch",
    // which tells an athlete nothing (ai-trainer-ops#95). An abort is the
    // caller's own doing and stays what it is.
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
    if (response.status === 401 && token && typeof window !== 'undefined') {
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
}
