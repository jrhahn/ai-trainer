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
