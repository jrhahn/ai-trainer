/**
 * Ending sessions that are already signed in (#704).
 *
 * The token lives in `sessionStorage`, so closing the tab ends the session on
 * *this* machine. Nothing here is about that case — it is about the sessions
 * you cannot reach: a token copied off a shared computer, or a browser left
 * signed in somewhere you no longer have.
 */
import { apiFetch } from './api'

export interface SessionRevokeResult {
  /** The generation now in force; every token below it is dead. */
  tokenGeneration: number
}

/**
 * Sign out everywhere, including the caller.
 *
 * Needs the password for the same reason turning off two-factor does: a
 * borrowed unlocked browser must not be able to lock the owner out. Trusted
 * devices go with it — the reason to press this is that a device is gone.
 */
export function revokeAllSessions(
  token: string,
  password: string
): Promise<SessionRevokeResult> {
  return apiFetch<SessionRevokeResult>('/auth/sessions/revoke', {
    method: 'POST',
    token,
    body: { password },
  })
}
