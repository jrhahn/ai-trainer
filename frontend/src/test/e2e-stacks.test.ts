/**
 * The two end-to-end stacks share nothing they must not share
 * (ai-trainer-ops#46).
 *
 * A unit test for configuration, which is unusual and earns its place here: the
 * failure it guards is invisible when the suites are run the way CI runs them.
 * Both stacks built into `dist/` at first, so two running at once had the second
 * build overwrite the first — the keyless preview would serve a bundle pointing
 * at the stubbed backend, and its 402 assertions would quietly stop testing
 * anything. CI never hit it because its steps are sequential. Raised in review,
 * and exactly the kind of trap that waits for the first person to open two
 * terminals.
 *
 * Asserting on the configs rather than on a comment, because "these can run at
 * once" was a claim in a comment and the comment was wrong.
 */

import { describe, expect, it } from 'vitest'

import keyless from '../../playwright.config'
import stubbed from '../../playwright.stub.config'

/** `webServer` is one entry or several; these configs always use two. */
function servers(config: typeof keyless) {
  const value = config.webServer
  return Array.isArray(value) ? value : value ? [value] : []
}

function backendEnv(config: typeof keyless): Record<string, string> {
  return (servers(config)[0]?.env ?? {}) as Record<string, string>
}

describe('the two e2e stacks', () => {
  it('disagree about the one thing they exist to disagree about', () => {
    // The whole reason there are two. The stub is chosen before the key checks,
    // so a stack with it on cannot also exercise the keyless path.
    expect(backendEnv(keyless).AI_STUB_PROVIDER).toBeUndefined()
    expect(backendEnv(stubbed).AI_STUB_PROVIDER).toBe('true')
    expect(backendEnv(keyless).ALLOW_ADMIN_AI_KEY_FALLBACK).toBe('false')
  })

  it('build into separate directories', () => {
    // The review's finding. Without this the bundles overwrite each other and
    // one preview serves the other's backend URL.
    const [, keylessFrontend] = servers(keyless)
    const [, stubbedFrontend] = servers(stubbed)

    expect(keylessFrontend.command).toContain('--outDir dist-keyless')
    expect(stubbedFrontend.command).toContain('--outDir dist-stubbed')
    expect(keylessFrontend.command).not.toContain('dist-stubbed')
    expect(stubbedFrontend.command).not.toContain('dist-keyless')
  })

  it('use separate ports, so both can be up at the same time', () => {
    const ports = (config: typeof keyless) =>
      servers(config).map((server) => new URL(server.url!).port)

    expect(new Set([...ports(keyless), ...ports(stubbed)]).size).toBe(4)
  })

  it('use separate databases, so neither inherits the other’s accounts', () => {
    expect(backendEnv(keyless).DATABASE_URL).not.toBe(backendEnv(stubbed).DATABASE_URL)
  })

  it('still agree about everything that took measuring to get right', () => {
    // The point of the shared factory. If these drift, the next thing learned
    // about running this stack gets fixed in one place and not the other.
    for (const config of [keyless, stubbed]) {
      const [, frontend] = servers(config)
      // `vite preview` binds to ::1 without it, so an IPv4 health check is
      // refused while the server is up and serving.
      expect(frontend.command).toContain('--host 127.0.0.1')
      // The global registration cap is 5/hour and the suites register more.
      expect(backendEnv(config).REGISTRATION_RATE_LIMIT_ATTEMPTS).toBe('100')
      // The stub refuses to start outside a development environment.
      expect(backendEnv(config).APP_ENV).toBe('test')
      // One worker: the specs share a SQLite file.
      expect(config.workers).toBe(1)
    }
  })
})
