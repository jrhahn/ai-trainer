import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
const mockSetCsrfToken = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({
  apiFetch: mockApiFetch,
  setCsrfToken: mockSetCsrfToken,
  newSessionMarker: () => 'cookie-session:marker',
}))

import { login, register, getSessionToken, resumeSession, endSession } from './auth'

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
  it('starts a cookie session and keeps its CSRF token', async () => {
    mockApiFetch.mockResolvedValue({ csrfToken: 'csrf-jwt-token-abc' })

    const token = await login('alice@example.com', 'password123')

    expect(token).toBe('cookie-session:marker')
    expect(mockSetCsrfToken).toHaveBeenCalledWith('csrf-jwt-token-abc')
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

  it('starts a cookie session when registration signs in', async () => {
    mockRegisterFlow({
      challenge: null,
      result: { csrfToken: 'csrf-new-jwt-token' },
    })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe('cookie-session:marker')
    expect(mockSetCsrfToken).toHaveBeenCalledWith('csrf-new-jwt-token')
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
      result: { csrfToken: 'csrf-new-jwt-token' },
    })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe('cookie-session:marker')
    expect(mockSetCsrfToken).toHaveBeenCalledWith('csrf-new-jwt-token')
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
      result: { csrfToken: 'csrf-unreachable' },
    })

    await expect(register('Alice', 'alice@example.com', 'securepass')).rejects.toThrow()
    expect(mockApiFetch).not.toHaveBeenCalledWith('/auth/register', expect.anything())
  })
})

describe('getSessionToken', () => {
  it('starts a cookie session from the Authelia session endpoint', async () => {
    mockApiFetch.mockResolvedValue({ csrfToken: 'csrf-session-token' })

    const token = await getSessionToken()

    expect(token).toBe('cookie-session:marker')
    expect(mockSetCsrfToken).toHaveBeenCalledWith('csrf-session-token')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/session')
  })
})

describe('resumeSession (ai-trainer-ops#45)', () => {
  it('asks once per page load, however often it is called', async () => {
    mockApiFetch.mockResolvedValue({ csrfToken: 'csrf-resumed' })

    const [first, second] = await Promise.all([resumeSession(), resumeSession()])

    expect(first).toBe('cookie-session:marker')
    expect(second).toBe(first)
    expect(mockApiFetch).toHaveBeenCalledTimes(1)
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/resume')
    expect(mockSetCsrfToken).toHaveBeenCalledWith('csrf-resumed')
  })

  it('reports no session when the server answers 204, and asks again after a sign-out', async () => {
    await endSession()
    mockApiFetch.mockResolvedValueOnce(undefined)

    await expect(resumeSession()).resolves.toBeNull()
  })

  it('reports no session when the request fails', async () => {
    await endSession()
    mockApiFetch.mockRejectedValueOnce(new Error('Not Found'))

    await expect(resumeSession()).resolves.toBeNull()
  })
})

describe('endSession', () => {
  it('forgets the CSRF token and clears the cookie, without throwing', async () => {
    mockApiFetch.mockRejectedValue(new Error('offline'))

    await expect(endSession()).resolves.toBeUndefined()

    expect(mockSetCsrfToken).toHaveBeenCalledWith(null)
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
  })
})

describe('a server that still answers with a bearer token', () => {
  it('is used as before, so a frontend deployed ahead of its backend keeps working', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'jwt-from-old-backend', token_type: 'bearer' })

    await expect(login('alice@example.com', 'pw')).resolves.toBe('jwt-from-old-backend')
    expect(mockSetCsrfToken).toHaveBeenCalledWith(null)
  })
})
