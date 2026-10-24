import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { accountKey, readLocal, writeLocal } from './storage'

const SRC = join(__dirname, '..')

/** The app's own source files, tests left out. */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

describe('lib/storage (ai-trainer-ops#269)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('reads back what it wrote, and null for nothing', () => {
    writeLocal('key', 'value')
    expect(readLocal('key')).toBe('value')
    expect(readLocal('missing')).toBeNull()
  })

  it('reads null and writes nothing when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(readLocal('key')).toBeNull()
    expect(() => writeLocal('key', 'value')).not.toThrow()
  })

  it('keys a value by its account', () => {
    expect(accountKey('ai_trainer_previous_login', 'a@example.com')).toBe(
      'ai_trainer_previous_login:a@example.com',
    )
  })

  it('is the only place that touches localStorage', () => {
    const offenders = sources(SRC).filter(
      (path) => !path.endsWith(join('lib', 'storage.ts')) && readFileSync(path, 'utf8').includes('localStorage'),
    )
    expect(offenders.map((path) => relative(SRC, path))).toEqual([])
  })
})
