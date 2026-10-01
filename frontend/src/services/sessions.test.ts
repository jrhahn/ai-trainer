/**
 * What signing out does, and what it deliberately does not do (#704).
 *
 * Both sign-out buttons used to navigate to `${AUTHELIA_URL}/logout`, which
 * left the athlete stranded on the Authelia portal. Adding `?rd=` did not fix
 * it: Authelia logged nothing for the attempt, because an app user has no
 * Authelia session to log out of — sign-in reads `users_database.yml` directly
 * and never touches the portal (#324). These tests pin the conclusion, so the
 * redirect is not reintroduced as an obvious-looking improvement.
 */
import { describe, expect, it, vi } from 'vitest'
import { endSession } from './sessions'

describe('endSession', () => {
  it('drops the app token', () => {
    const clear = vi.fn()

    endSession(clear)

    expect(clear).toHaveBeenCalledTimes(1)
  })

  it('does not navigate anywhere', () => {
    const clear = vi.fn()
    const assign = vi.fn()
    const original = window.location
    Object.defineProperty(window, 'location', {
      value: {
        origin: 'https://trainlikea.pro',
        set href(value: string) {
          assign(value)
        },
      },
      writable: true,
      configurable: true,
    })

    try {
      endSession(clear)
      // The app renders its own landing page once the token is gone. Sending
      // the browser to auth.<domain> is what produced the dead end: that page
      // cannot sign anyone in here while forward-auth is attached to no
      // router (#696).
      expect(assign).not.toHaveBeenCalled()
    } finally {
      Object.defineProperty(window, 'location', {
        value: original,
        writable: true,
        configurable: true,
      })
    }
  })

  it('exposes no Authelia logout URL to call', async () => {
    const module = await import('./sessions')

    // Keeping a helper nobody calls is how the redirect comes back. If
    // forward-auth is ever enabled, reintroduce it on purpose and with a test
    // that asserts the navigation.
    expect(Object.keys(module).sort()).toEqual(['endSession', 'revokeAllSessions'])
  })
})
