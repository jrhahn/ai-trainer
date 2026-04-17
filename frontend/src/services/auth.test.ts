import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import { login, register, getSessionToken } from './auth'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('login', () => {
  it('returns the access_token from the backend', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'jwt-token-abc', token_type: 'bearer' })

    const token = await login('alice@example.com', 'password123')

    expect(token).toBe('jwt-token-abc')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/login', {
      method: 'POST',
      body: { email: 'alice@example.com', password: 'password123' },
    })
  })
})

describe('register', () => {
  it('returns the access_token when registration returns a token', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'new-jwt-token', token_type: 'bearer' })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe('new-jwt-token')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: { name: 'Alice', email: 'alice@example.com', password: 'securepass' },
    })
  })

  it('returns null when registration succeeds without a token (Authelia mode)', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBeNull()
  })
})

describe('getSessionToken', () => {
  it('returns the access_token from the session endpoint', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'session-token', token_type: 'bearer' })

    const token = await getSessionToken()

    expect(token).toBe('session-token')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/session')
  })
})
