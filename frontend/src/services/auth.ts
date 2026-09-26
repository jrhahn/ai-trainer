import { apiFetch } from './api'
import { solveCaptcha, type CaptchaChallenge, type CaptchaSolution } from './captcha'

interface AuthResponse {
  access_token: string
  token_type: string
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

export async function register(name: string, email: string, password: string): Promise<string | null> {
  const captcha = await obtainCaptcha()
  const response = await apiFetch<{ access_token: string; token_type: string } | undefined>('/auth/register', {
    method: 'POST',
    body: captcha ? { name, email, password, captcha } : { name, email, password },
  })
  return response?.access_token ?? null
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
  const response = await apiFetch<AuthResponse | LoginChallenge>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  if ('mfaRequired' in response && response.mfaRequired) {
    return { kind: 'mfa', challenge: response.challenge }
  }
  return { kind: 'token', token: (response as AuthResponse).access_token }
}

/** Exchange a challenge and a code — or a recovery code — for a token. */
export async function loginWithTotp(
  challenge: string,
  code: string,
  rememberDevice: boolean,
): Promise<string> {
  const response = await apiFetch<AuthResponse>('/auth/login/totp', {
    method: 'POST',
    body: { challenge, code, rememberDevice },
  })
  return response.access_token
}

export async function login(email: string, password: string): Promise<string> {
  const response = await apiFetch<AuthResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  return response.access_token
}

export async function getSessionToken(): Promise<string> {
  const response = await apiFetch<AuthResponse>('/auth/session')
  return response.access_token
}
