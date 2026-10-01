/**
 * Ending sessions that are already signed in (#704).
 *
 * The token lives in `sessionStorage`, so closing the tab ends the session on
 * *this* machine. Nothing here is about that case — it is about the sessions
 * you cannot reach: a token copied off a shared computer, or a browser left
 * signed in somewhere you no longer have.
 */
import { apiFetch, AUTHELIA_URL } from './api'

/**
 * Where to send the browser so Authelia's own session ends too, or null.
 *
 * Authelia is the user store here, and it keeps a session cookie of its own on
 * the parent domain. Dropping only the app token would leave that standing —
 * which matters most for the one control that promises *every* session.
 *
 * `rd` is the part that was missing. Without it, Authelia logs the user out and
 * leaves them sitting on `auth.<domain>`, a portal that cannot sign them in to
 * this app at all: forward-auth is attached to no router (#696), so the app's
 * own login page is the only way back in. Landing there looks like being
 * dumped somewhere broken, because it is.
 *
 * The return target comes from `location.origin` rather than a constant: two
 * domains are configured in `authelia/configuration.yml`, and Authelia rejects
 * an `rd` outside the cookie domain it issued the session for. Using whichever
 * domain the user is actually on is correct for both.
 */
export function autheliaLogoutUrl(
  returnTo: string = typeof window === 'undefined' ? '' : window.location.origin
): string | null {
  if (!AUTHELIA_URL) return null
  return `${AUTHELIA_URL}/logout?rd=${encodeURIComponent(returnTo)}`
}

/**
 * End the session in this browser: drop the app token, then Authelia's cookie.
 *
 * The one definition of "sign out", shared by the nav, the Account card and
 * "sign out everywhere". Three call sites spelling it out separately is how one
 * of them ends up forgetting half of it.
 */
export function endSession(clearToken: () => void): void {
  clearToken()
  const url = autheliaLogoutUrl()
  if (url) {
    window.location.href = url
  }
}

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
