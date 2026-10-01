/**
 * Where signing out sends the browser (#704).
 *
 * This was untested, which is why nobody noticed that `${AUTHELIA_URL}/logout`
 * with no `rd` leaves the athlete stranded on the Authelia portal — a page that
 * cannot sign them in to this app, because forward-auth is attached to no
 * router (#696).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

async function load(autheliaUrl: string) {
  vi.resetModules()
  vi.doMock('./api', () => ({
    AUTHELIA_URL: autheliaUrl,
    apiFetch: vi.fn(),
  }))
  return import('./sessions')
}

afterEach(() => {
  vi.doUnmock('./api')
  vi.resetModules()
})

describe('autheliaLogoutUrl', () => {
  it('sends the athlete back to the app, not to the portal', async () => {
    const { autheliaLogoutUrl } = await load('https://auth.trainlikea.pro')

    expect(autheliaLogoutUrl('https://trainlikea.pro')).toBe(
      'https://auth.trainlikea.pro/logout?rd=https%3A%2F%2Ftrainlikea.pro'
    )
  })

  it('returns null when no Authelia is configured', async () => {
    const { autheliaLogoutUrl } = await load('')

    // Development runs without it, and a bare `/logout` against an empty base
    // would navigate to the wrong origin entirely.
    expect(autheliaLogoutUrl('http://localhost:5173')).toBeNull()
  })

  it('defaults to the origin the athlete is actually on', async () => {
    const { autheliaLogoutUrl } = await load('https://auth.trainlikea.pro')

    // Two domains are configured in authelia/configuration.yml and Authelia
    // rejects an `rd` outside the cookie domain of the session it issued, so a
    // hardcoded return target would be wrong on one of them.
    expect(autheliaLogoutUrl()).toBe(
      `https://auth.trainlikea.pro/logout?rd=${encodeURIComponent(window.location.origin)}`
    )
  })

  it('escapes the return target rather than pasting it in', async () => {
    const { autheliaLogoutUrl } = await load('https://auth.trainlikea.pro')

    expect(autheliaLogoutUrl('https://trainlikea.pro/?next=/settings')).toContain(
      'rd=https%3A%2F%2Ftrainlikea.pro%2F%3Fnext%3D%2Fsettings'
    )
  })
})

describe('endSession', () => {
  it('drops the app token before navigating away', async () => {
    const { endSession } = await load('https://auth.trainlikea.pro')
    const clear = vi.fn()
    const assign = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { origin: 'https://trainlikea.pro', set href(v: string) { assign(v) } },
      writable: true,
      configurable: true,
    })

    endSession(clear)

    // Order matters: the navigation may not be interruptible, and leaving the
    // token in sessionStorage because a redirect raced it would mean the next
    // tab is still signed in.
    expect(clear).toHaveBeenCalled()
    expect(assign).toHaveBeenCalledWith(
      'https://auth.trainlikea.pro/logout?rd=https%3A%2F%2Ftrainlikea.pro'
    )
  })

  it('still clears the token when there is nowhere to redirect', async () => {
    const { endSession } = await load('')
    const clear = vi.fn()

    endSession(clear)

    expect(clear).toHaveBeenCalled()
  })
})
