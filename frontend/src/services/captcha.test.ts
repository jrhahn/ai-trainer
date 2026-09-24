import { describe, expect, it } from 'vitest'

import { solveCaptcha } from './captcha'

async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

function challengeFor(digest: string, maxnumber = 100) {
  return {
    algorithm: 'SHA-256',
    challenge: digest,
    salt: 'test-salt',
    signature: 'server-signature',
    maxnumber,
  }
}

describe('solveCaptcha', () => {
  it('finds the number behind the digest', async () => {
    const solution = await solveCaptcha(challengeFor(await sha256Hex('test-salt42')))

    expect(solution.number).toBe(42)
  })

  it('echoes the challenge back unchanged so the server can verify its signature', async () => {
    const digest = await sha256Hex('test-salt7')

    const solution = await solveCaptcha(challengeFor(digest))

    expect(solution).toEqual({
      challenge: digest,
      salt: 'test-salt',
      signature: 'server-signature',
      number: 7,
    })
  })

  it('solves zero, which a loop starting at one would miss', async () => {
    const solution = await solveCaptcha(challengeFor(await sha256Hex('test-salt0')))

    expect(solution.number).toBe(0)
  })

  it('solves the last candidate, which an exclusive bound would miss', async () => {
    const solution = await solveCaptcha(challengeFor(await sha256Hex('test-salt10'), 10))

    expect(solution.number).toBe(10)
  })

  it('throws rather than returning a wrong answer when there is no solution', async () => {
    await expect(solveCaptcha(challengeFor('f'.repeat(64), 20))).rejects.toThrow(
      /could not solve/i,
    )
  })

  it('refuses an algorithm it does not implement', async () => {
    const challenge = { ...challengeFor('abc'), algorithm: 'SHA-512' }

    await expect(solveCaptcha(challenge)).rejects.toThrow(/unsupported/i)
  })
})
