export const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined ?? 'http://localhost:8000').replace(/\/$/, '')

export const AUTHELIA_URL =
  ((import.meta.env.VITE_AUTHELIA_URL as string | undefined) ?? '').replace(/\/$/, '')

export const API_BASE = `${BACKEND_URL}/api/v1`

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

interface ApiFetchOptions extends Omit<RequestInit, 'body'> {
  token?: string | null
  body?: unknown
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { token, headers, body, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  const requestId = generateRequestId()
  const timezone = getBrowserTimezone()

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: init.credentials ?? 'include',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'X-Request-ID': requestId,
      ...(timezone ? { 'X-App-Timezone': timezone } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

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
    console.error('[api] Request failed', {
      method,
      path,
      status: response.status,
      contentType: response.headers.get('Content-Type'),
      requestId: serverRequestId,
      ...(parseFailReason !== undefined ? { parseFailReason } : {}),
    })
    throw new Error(message)
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
