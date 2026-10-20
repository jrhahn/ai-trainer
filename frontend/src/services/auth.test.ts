import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
const mockSetCsrfToken = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({
  apiFetch: mockApiFetch,
  setCsrfToken: mockSetCsrfToken,
  newSessionMarker: () => 'cookie-session:marker',
}))

import {
  fetchSignupConfig,
  login,
  register,
  getSessionToken,
  resumeSession,
  endSession,
  onSignedOutElsewhere,
  RESUME_TIMEOUT_MS,
  requestPasswordReset,
  confirmPasswordReset,
  changePassword,
  CONFIRM_EMAIL,
  verifyEmail,
  resendEmailVerification,
} from './auth'
import { queryClient } from '../lib/queryClient'
import { ONBOARDING_STORAGE_KEY } from '../lib/onboardingStorage'

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

  it('sends an invitation code, trimmed, only when there is one (ops#108)', async () => {
    mockRegisterFlow({ challenge: null, result: { csrfToken: 'csrf' } })

    await register('Alice', 'alice@example.com', 'securepass', '  K7QM-3XRP-WD ')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: { name: 'Alice', email: 'alice@example.com', password: 'securepass', inviteCode: 'K7QM-3XRP-WD' },
    })
  })

  it('sends the consent to the privacy policy when it was given (ai-trainer-ops#2)', async () => {
    mockRegisterFlow({ challenge: null, result: { csrfToken: 'csrf' } })

    await register('Alice', 'alice@example.com', 'securepass', undefined, true)

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: { name: 'Alice', email: 'alice@example.com', password: 'securepass', privacyConsent: true },
    })
  })

  it('asks the server whether sign-up needs an invitation', async () => {
    mockApiFetch.mockResolvedValueOnce({ inviteOnly: true })

    expect(await fetchSignupConfig()).toEqual({ inviteOnly: true })
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/signup')
  })

  it('says the address has to be confirmed first when the server sent a link (app #687)', async () => {
    mockRegisterFlow({ challenge: null, result: { verificationRequired: true } })

    const token = await register('Alice', 'alice@example.com', 'securepass')

    expect(token).toBe(CONFIRM_EMAIL)
    expect(mockSetCsrfToken).not.toHaveBeenCalled()
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

describe('resumeSession on a connection that hangs', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('gives up after the timeout, and ignores an answer that arrives later', async () => {
    await endSession()
    vi.clearAllMocks()
    vi.useFakeTimers()
    let answer: (value: unknown) => void = () => {}
    mockApiFetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))

    const resumed = resumeSession()
    await vi.advanceTimersByTimeAsync(RESUME_TIMEOUT_MS)
    await expect(resumed).resolves.toBeNull()

    answer({ csrfToken: 'csrf-too-late' })
    await vi.runAllTimersAsync()
    expect(mockSetCsrfToken).not.toHaveBeenCalledWith('csrf-too-late')
  })
})

describe('resumeSession on a connection that answers', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('leaves no timer running once the server has answered', async () => {
    await endSession()
    vi.useFakeTimers()
    mockApiFetch.mockResolvedValueOnce(undefined)

    await resumeSession()

    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('endSession', () => {
  it('forgets the CSRF token and clears the cookie, without throwing', async () => {
    mockApiFetch.mockRejectedValue(new Error('offline'))

    await expect(endSession()).resolves.toBeUndefined()

    expect(mockSetCsrfToken).toHaveBeenCalledWith(null)
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
  })

  it('clears the query cache and onboarding progress for the next account (ops#130)', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    const clearSpy = vi.spyOn(queryClient, 'clear')
    sessionStorage.setItem(ONBOARDING_STORAGE_KEY, '{"step":2}')

    await endSession()

    expect(clearSpy).toHaveBeenCalled()
    expect(sessionStorage.getItem(ONBOARDING_STORAGE_KEY)).toBeNull()
    clearSpy.mockRestore()
  })
})

describe('signing out in another tab (ops#130)', () => {
  const KEY = 'ai-trainer:signed-out'

  it('tells the other tabs, and a tab that was told does not tell them back', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    await endSession()
    const announced = localStorage.getItem(KEY)
    expect(announced).not.toBeNull()

    // The other tab: the event runs its sign-out, which neither announces it
    // again nor calls the server, whose cookie the first tab already ended.
    mockApiFetch.mockClear()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const signOut = vi.fn(() => void endSession())
    const stop = onSignedOutElsewhere(signOut)
    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: announced }))

    expect(signOut).toHaveBeenCalledTimes(1)
    expect(mockSetCsrfToken).toHaveBeenCalledWith(null)
    expect(setItem).not.toHaveBeenCalledWith(KEY, expect.anything())
    expect(mockApiFetch).not.toHaveBeenCalled()

    // Another key is no sign-out, and once stopped nothing is heard.
    window.dispatchEvent(new StorageEvent('storage', { key: 'something-else' }))
    stop()
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect(signOut).toHaveBeenCalledTimes(1)
    setItem.mockRestore()
  })

  it('writes a new value on every sign-out, so every one of them is an event', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    vi.spyOn(Date, 'now').mockReturnValue(1_760_000_000_000)
    await endSession()
    const first = localStorage.getItem(KEY)
    await endSession()
    expect(localStorage.getItem(KEY)).not.toBe(first)
    vi.restoreAllMocks()
  })

  it('still signs out here when the browser keeps no storage', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private window')
    })

    await expect(endSession()).resolves.toBeUndefined()
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
    setItem.mockRestore()
  })
})

describe('a server that still answers with a bearer token', () => {
  it('is used as before, so a frontend deployed ahead of its backend keeps working', async () => {
    mockApiFetch.mockResolvedValue({ access_token: 'jwt-from-old-backend', token_type: 'bearer' })

    await expect(login('alice@example.com', 'pw')).resolves.toBe('jwt-from-old-backend')
    expect(mockSetCsrfToken).toHaveBeenCalledWith(null)
  })
})


describe('password reset (ai-trainer-ops#35)', () => {
  it('asks for a link and returns what the server says', async () => {
    mockApiFetch.mockResolvedValue({ detail: 'If this address has an account, a link is on its way.' })

    expect(await requestPasswordReset('rider@example.com')).toBe('If this address has an account, a link is on its way.')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/password-reset', {
      method: 'POST',
      body: { email: 'rider@example.com' },
    })
  })

  it('sends the token and the new password', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await confirmPasswordReset('token-123', 'gravel climbs at dawn')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/password-reset/confirm', {
      method: 'POST',
      body: { token: 'token-123', password: 'gravel climbs at dawn' },
    })
  })
})

describe('changing the password while signed in (ai-trainer-ops#172)', () => {
  it('sends the current and the new password with the session', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await changePassword('tok', 'Str0ng!Pass', 'gravel climbs at dawn')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/password', {
      method: 'POST',
      token: 'tok',
      body: { currentPassword: 'Str0ng!Pass', newPassword: 'gravel climbs at dawn' },
    })
  })
})


describe('confirming an address (app #687)', () => {
  it('posts the token from the link', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await verifyEmail('user-1.1900000000.abcdef')

    expect(mockApiFetch).toHaveBeenCalledWith('/auth/verify-email', {
      method: 'POST',
      body: { token: 'user-1.1900000000.abcdef' },
    })
  })

  it('asks for a new link and passes on the answer', async () => {
    mockApiFetch.mockResolvedValue({ detail: 'A new link is on its way.' })

    expect(await resendEmailVerification('rider@example.com')).toBe('A new link is on its way.')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/verify-email/resend', {
      method: 'POST',
      body: { email: 'rider@example.com' },
    })
  })
})
