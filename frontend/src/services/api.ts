export const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined ?? 'http://localhost:8000').replace(/\/$/, '')

export const AUTHELIA_URL =
  ((import.meta.env.VITE_AUTHELIA_URL as string | undefined) ?? '').replace(/\/$/, '')

export const API_BASE = `${BACKEND_URL}/api/v1`

interface ApiFetchOptions extends Omit<RequestInit, 'body'> {
  token?: string | null
  body?: unknown
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { token, headers, body, ...init } = options
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) {
    let message = 'Request failed'
    try {
      const data = await response.json() as { detail?: string }
      if (data.detail) message = data.detail
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}
