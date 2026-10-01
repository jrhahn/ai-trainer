/**
 * Ending sessions that are already signed in (#704).
 *
 * The token lives in `sessionStorage`, so closing the tab ends the session on
 * *this* machine. Nothing here is about that case — it is about the sessions
 * you cannot reach: a token copied off a shared computer, or a browser left
 * signed in somewhere you no longer have.
 */
import { apiFetch } from './api'

/**
 * End the session in this browser: drop the app token. That is the whole of it.
 *
 * The one definition of "sign out", shared by the nav, the Account card and
 * "sign out everywhere", so that three call sites cannot drift apart.
 *
 * **There is deliberately no Authelia logout here, and that is the fix.** Both
 * sign-out buttons used to navigate to `${AUTHELIA_URL}/logout`, on the belief
 * that Authelia holds a session of its own that has to be ended too. It does
 * not — not for an app user. Sign-in goes through `/auth/login`, which reads
 * `users_database.yml` directly and never touches Authelia's portal (#324), so
 * no Authelia session is ever created. The portal's logout had nothing to end,
 * so it did nothing and left the athlete parked on `auth.<domain>`: a page that
 * cannot sign them back in, because forward-auth is attached to no router
 * (#696).
 *
 * Adding `?rd=` did not help, which is what proved the diagnosis — Authelia
 * logged *nothing at all* for the attempt, because the SPA only performs the
 * return redirect after a logout it actually carried out. The navigation was
 * never the problem; going there at all was.
 *
 * If forward-auth is ever wired up, Authelia *will* hold the session and this
 * becomes wrong in the other direction. That is the moment to bring the logout
 * call back — along with `/auth/session` and the probe in `App.tsx`, which
 * belong to the same dormant design.
 */
export function endSession(clearToken: () => void): void {
  clearToken()
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
