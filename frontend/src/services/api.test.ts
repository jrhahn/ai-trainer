import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockFetch = vi.hoisted(() => vi.fn())
vi.stubGlobal('fetch', mockFetch)

import { apiFetch } from './api'

function makeResponse(status: number, body: unknown): Response {
  const jsonStr = JSON.stringify(body)
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    headers: new Headers({ 'Content-Type': 'application/json' }),
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
      expect.objectContaining({ headers: {} })
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
    mockFetch.mockResolvedValue({ ok: true, status: 204, json: vi.fn() } as unknown as Response)

    const result = await apiFetch<undefined>('/test', { method: 'DELETE' })

    expect(result).toBeUndefined()
  })

  it('throws with the detail message from a non-OK JSON response', async () => {
    mockFetch.mockResolvedValue(makeResponse(400, { detail: 'Invalid input' }))

    await expect(apiFetch('/test')).rejects.toThrow('Invalid input')
  })

  it('throws a generic message when the error response has no detail', async () => {
    mockFetch.mockResolvedValue(makeResponse(500, { message: 'Server error' }))

    await expect(apiFetch('/test')).rejects.toThrow('Request failed')
  })

  it('throws a generic message when the error response is not JSON', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.reject(new SyntaxError('not json')),
    } as unknown as Response)

    await expect(apiFetch('/test')).rejects.toThrow('Request failed')
  })
})
