import { describe, expect, it } from 'vitest'
import { formatLocalDate, parseLocalDate } from './workout'

describe('workout date helpers', () => {
  it('formats dates from local calendar fields instead of UTC ISO conversion', () => {
    const date = new Date(2026, 4, 18, 0, 30)

    expect(formatLocalDate(date)).toBe('2026-05-18')
  })

  it('parses ISO dates at local noon', () => {
    const date = parseLocalDate('2026-05-18')

    expect(date.getFullYear()).toBe(2026)
    expect(date.getMonth()).toBe(4)
    expect(date.getDate()).toBe(18)
    expect(date.getHours()).toBe(12)
  })
})
