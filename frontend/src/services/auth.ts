import { apiFetch, newSessionMarker, setCsrfToken } from './api'
import { solveCaptcha, type CaptchaChallenge, type CaptchaSolution } from './captcha'
import { queryClient } from '../lib/queryClient'
import { clearOnboardingProgress } from '../lib/onboardingStorage'

/**
 * What every sign-in endpoint answers for the web app (ai-trainer-ops#45): the
 * session itself went into an HttpOnly cookie, and this is the CSRF token that
 * goes with it. Returns the marker the store keeps as `authToken`.
 */
interface SessionResponse {
  csrfToken: string
}

/** What a server that ignores `X-Auth-Mode` answers: a backend older than #45, mid-deploy. */
interface TokenResponse {
  access_token: string
}

function startSession(response: SessionResponse | TokenResponse): string {
  if ('access_token' in response) {
    setCsrfToken(null)
    return response.access_token
  }
  setCsrfToken(response.csrfToken)
  return newSessionMarker()
}

let resuming: Promise<string | null> | null = null

// The app renders nothing until the session check answers, so a request that
// hangs (a phone waking from sleep on a dead connection) must not hold the page
// forever. Found in review on PR #808.
export const RESUME_TIMEOUT_MS = 10_000

/**
 * Whether this browser still holds a cookie session, asked once per page load.
 *
 * Single-flight on purpose: each call re-issues the cookie with a new CSRF
 * token, and two overlapping calls (React StrictMode runs effects twice) could
 * leave the cookie from one beside the CSRF token from the other.
 */
export function resumeSession(): Promise<string | null> {
  if (!resuming) {
    let timedOut = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<null>((resolve) => {
      timer = setTimeout(() => {
        timedOut = true
        resolve(null)
      }, RESUME_TIMEOUT_MS)
    })
    // 204 (no body) is "no session"; so is an error, including a backend from
    // before #45 that has no such route. An answer after the timeout is
    // dropped: by then the athlete may have signed in again, and its CSRF token
    // would overwrite the new one.
    const request = apiFetch<SessionResponse | undefined>('/auth/resume').then(
      (response) => (response && !timedOut ? startSession(response) : null),
      () => null,
    ).finally(() => clearTimeout(timer))
    resuming = Promise.race([request, timeout])
  }
  return resuming
}

/** End this browser's cookie session. Never throws: signing out locally must not wait on it. */
export async function endSession(): Promise<void> {
  setCsrfToken(null)
  resuming = null
  // On a shared device the next account must not see this one's data: drop the
  // whole react-query cache and any half-finished onboarding progress (ops#130).
  // Synchronous, before the network round-trip, so it happens even if that fails.
  queryClient.clear()
  clearOnboardingProgress()
  try {
    await apiFetch<void>('/auth/logout', { method: 'POST' })
  } catch {
    // The cookie expires on its own; the local sign-out has already happened.
  }
}

/**
 * Fetch and solve a registration proof-of-work (#686).
 *
 * Returns `null` when the server does not issue challenges — a deployment with
 * CAPTCHA_ENABLED=false answers 404, and registration there takes no solution.
 * Any other failure throws, because submitting a form that is certain to be
 * rejected helps nobody.
 */
async function obtainCaptcha(): Promise<CaptchaSolution | null> {
  let challenge: CaptchaChallenge
  try {
    challenge = await apiFetch<CaptchaChallenge>('/auth/captcha/challenge')
  } catch {
    return null
  }
  return solveCaptcha(challenge)
}

/** What `register` returns when the account has to confirm its address before
 * it can sign in (app #687): the server sends mail, so it sent a link. */
export const CONFIRM_EMAIL = 'confirm-email'

export async function register(
  name: string,
  email: string,
  password: string,
): Promise<string | typeof CONFIRM_EMAIL | null> {
  const captcha = await obtainCaptcha()
  const response = await apiFetch<
    SessionResponse | TokenResponse | { verificationRequired: true } | undefined
  >('/auth/register', {
    method: 'POST',
    body: captcha ? { name, email, password, captcha } : { name, email, password },
  })
  if (response && 'verificationRequired' in response) return CONFIRM_EMAIL
  return response ? startSession(response) : null
}

/** Confirm an account's address from the link it was mailed (app #687). */
export async function verifyEmail(token: string): Promise<void> {
  await apiFetch<void>('/auth/verify-email', { method: 'POST', body: { token } })
}

/** Ask for a new confirmation link. The answer is the same for every address. */
export async function resendEmailVerification(email: string): Promise<string> {
  const response = await apiFetch<{ detail: string }>('/auth/verify-email/resend', {
    method: 'POST',
    body: { email },
  })
  return response.detail
}

/**
 * Either a token, or "the password was right, now show the code field" (#688).
 *
 * The server answers 200 for both: the password *was* accepted, so reporting
 * the second step as an auth failure would leave the caller unable to tell it
 * apart from a wrong password.
 */
export type LoginResult =
  | { kind: 'token'; token: string }
  | { kind: 'mfa'; challenge: string }

interface LoginChallenge {
  mfaRequired: boolean
  challenge: string
}

export async function loginStep(email: string, password: string): Promise<LoginResult> {
  const response = await apiFetch<SessionResponse | TokenResponse | LoginChallenge>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  if ('mfaRequired' in response && response.mfaRequired) {
    return { kind: 'mfa', challenge: response.challenge }
  }
  return { kind: 'token', token: startSession(response as SessionResponse | TokenResponse) }
}

/** Exchange a challenge and a code — or a recovery code — for a token. */
export async function loginWithTotp(
  challenge: string,
  code: string,
  rememberDevice: boolean,
): Promise<string> {
  const response = await apiFetch<SessionResponse | TokenResponse>('/auth/login/totp', {
    method: 'POST',
    body: { challenge, code, rememberDevice },
  })
  return startSession(response)
}

export async function login(email: string, password: string): Promise<string> {
  const response = await apiFetch<SessionResponse | TokenResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  return startSession(response)
}

export async function getSessionToken(): Promise<string> {
  const response = await apiFetch<SessionResponse | TokenResponse>('/auth/session')
  return startSession(response)
}

/** Ask for a reset link (ai-trainer-ops#35). The answer is the same whether
 * the address has an account; a 503 means this server sends no mail. */
export async function requestPasswordReset(email: string): Promise<string> {
  const response = await apiFetch<{ detail: string }>('/auth/password-reset', {
    method: 'POST',
    body: { email },
  })
  return response.detail
}

/** Set a new password from a reset link. Every session of the account ends. */
export async function confirmPasswordReset(token: string, password: string): Promise<void> {
  await apiFetch<void>('/auth/password-reset/confirm', { method: 'POST', body: { token, password } })
}
