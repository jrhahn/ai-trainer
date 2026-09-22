import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import { login, register, getSessionToken } from './auth'

async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

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
  /**
   * Answer the captcha challenge request, then the registration itself.
   *
   * `challenge: null` models a server with CAPTCHA_ENABLED=false, which 404s
   * the challenge endpoint (#686).
   */
  function mockRegisterFlow(options: { challenge: unknown | null; result: unknown }) {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === '/auth/captcha/challenge') {
        if (options.challenge === null) throw new Error('Not Found')
        return options.challenge
      }
      return options.result
    })
  }

  it('returns the access_token when registration returns a token', async () => {
    mockRegisterFlow({
      challenge: null,
      result: { access_token: 'new-jwt-token', token_type: 'bearer' },
    })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe('new-jwt-token')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: { name: 'Alice', email: 'alice@example.com', password: 'securepass' },
    })
  })

  it('returns null when registration succeeds without a token (Authelia mode)', async () => {
    mockRegisterFlow({ challenge: null, result: undefined })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBeNull()
  })

  it('solves the challenge and submits it alongside the credentials', async () => {
    // salt 'abc' with number 2 -> this digest; small so the solve is instant.
    const digest = await sha256Hex('abc2')
    mockRegisterFlow({
      challenge: {
        algorithm: 'SHA-256',
        challenge: digest,
        salt: 'abc',
        signature: 'server-signature',
        maxnumber: 100,
      },
      result: { access_token: 'new-jwt-token', token_type: 'bearer' },
    })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe('new-jwt-token')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: {
        name: 'Alice',
        email: 'alice@example.com',
        password: 'securepass',
        captcha: {
          challenge: digest,
          salt: 'abc',
          signature: 'server-signature',
          number: 2,
        },
      },
    })
  })

  it('does not submit the form when the challenge cannot be solved', async () => {
    mockRegisterFlow({
      challenge: {
        algorithm: 'SHA-256',
        challenge: 'f'.repeat(64),
        salt: 'abc',
        signature: 'server-signature',
        maxnumber: 5,
      },
      result: { access_token: 'unreachable', token_type: 'bearer' },
    })

    await expect(register('Alice', 'alice@example.com', 'securepass')).rejects.toThrow()
    expect(mockApiFetch).not.toHaveBeenCalledWith('/auth/register', expect.anything())
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
