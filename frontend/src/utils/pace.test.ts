import { describe, expect, it } from 'vitest'

import { formatPaceFromSeconds, parsePaceToSeconds } from './pace'

describe('parsePaceToSeconds', () => {
  it('reads a pace the way a runner writes one', () => {
    expect(parsePaceToSeconds('4:10')).toBe(250)
    expect(parsePaceToSeconds('12:00')).toBe(720)
    expect(parsePaceToSeconds(' 3:45 ')).toBe(225)
  })

  it('refuses a bare number rather than guessing what it meant', () => {
    expect(parsePaceToSeconds('4')).toBeNull()
    expect(parsePaceToSeconds('250')).toBeNull()
  })

  it('refuses a decimal, which is the mistake it exists to prevent', () => {
    expect(parsePaceToSeconds('4.17')).toBeNull()
  })

  it('refuses more than fifty-nine seconds', () => {
    expect(parsePaceToSeconds('4:60')).toBeNull()
    expect(parsePaceToSeconds('4:99')).toBeNull()
  })

  it('refuses nonsense', () => {
    expect(parsePaceToSeconds('')).toBeNull()
    expect(parsePaceToSeconds('fast')).toBeNull()
    expect(parsePaceToSeconds('0:00')).toBeNull()
    expect(parsePaceToSeconds('-4:10')).toBeNull()
  })
})

describe('formatPaceFromSeconds', () => {
  it('pads the seconds so 4:05 is not shown as 4:5', () => {
    expect(formatPaceFromSeconds(245)).toBe('4:05')
  })

  it('round-trips with the parser', () => {
    expect(parsePaceToSeconds(formatPaceFromSeconds(250))).toBe(250)
  })

  it('has nothing to show for a missing pace', () => {
    expect(formatPaceFromSeconds(null)).toBe('')
    expect(formatPaceFromSeconds(undefined)).toBe('')
    expect(formatPaceFromSeconds(0)).toBe('')
    expect(formatPaceFromSeconds(Number.NaN)).toBe('')
  })
})
