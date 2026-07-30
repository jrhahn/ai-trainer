import { describe, it, expect } from 'vitest'
import {
  isSameSession,
  sessionAtSlot,
  sessionKey,
  sessionLabel,
  sessionSlot,
  sessionsForDate,
} from './planSessions'

describe('sessionSlot', () => {
  it('reads a legacy day with no slot as the day\'s first session', () => {
    expect(sessionSlot({ date: '2026-08-04' })).toBe(0)
  })

  it('never invents a session from an unusable value', () => {
    expect(sessionSlot({ date: '2026-08-04', slot: -2 })).toBe(0)
    expect(sessionSlot({ date: '2026-08-04', slot: NaN })).toBe(0)
    expect(sessionSlot({ date: '2026-08-04', slot: 1.7 })).toBe(1)
    expect(sessionSlot(null)).toBe(0)
  })
})

describe('sessionKey / isSameSession', () => {
  it('keys on date and slot together, since a date can repeat', () => {
    expect(sessionKey({ date: '2026-08-04' })).toBe('2026-08-04#0')
    expect(sessionKey({ date: '2026-08-04', slot: 1 })).toBe('2026-08-04#1')
  })

  it('treats a slot-less day and slot 0 as the same session', () => {
    expect(isSameSession({ date: '2026-08-04' }, { date: '2026-08-04', slot: 0 })).toBe(true)
    expect(isSameSession({ date: '2026-08-04' }, { date: '2026-08-04', slot: 1 })).toBe(false)
    expect(isSameSession({ date: '2026-08-04' }, null)).toBe(false)
  })
})

describe('sessionsForDate', () => {
  const plan = [
    { date: '2026-08-04', slot: 1, title: 'PM endurance' },
    { date: '2026-08-03', title: 'Solo day' },
    { date: '2026-08-04', slot: 0, title: 'AM yoga' },
  ]

  it('returns every session on a date, in slot order', () => {
    expect(sessionsForDate(plan, '2026-08-04').map((d) => d.title)).toEqual([
      'AM yoga',
      'PM endurance',
    ])
  })

  it('returns the single session of an ordinary day', () => {
    expect(sessionsForDate(plan, '2026-08-03').map((d) => d.title)).toEqual(['Solo day'])
  })

  it('returns an empty array for an unplanned date, so callers can map', () => {
    expect(sessionsForDate(plan, '2026-08-09')).toEqual([])
    expect(sessionsForDate(null, '2026-08-04')).toEqual([])
    expect(sessionsForDate(plan, undefined)).toEqual([])
  })
})

describe('sessionAtSlot', () => {
  const plan = [
    { date: '2026-08-04', slot: 0, title: 'AM yoga' },
    { date: '2026-08-04', slot: 1, title: 'PM endurance' },
  ]

  it('selects the requested session', () => {
    expect(sessionAtSlot(plan, '2026-08-04', 1)?.title).toBe('PM endurance')
  })

  it('falls back to the first session for a missing or absent slot', () => {
    expect(sessionAtSlot(plan, '2026-08-04', undefined)?.title).toBe('AM yoga')
    expect(sessionAtSlot(plan, '2026-08-04', 7)?.title).toBe('AM yoga')
  })

  it('returns undefined when the date has nothing planned', () => {
    expect(sessionAtSlot(plan, '2026-08-05', 0)).toBeUndefined()
  })
})

describe('sessionLabel', () => {
  it('stays empty on a single-session day, so no phantom ordering appears', () => {
    expect(sessionLabel({ date: '2026-08-03' }, 1)).toBe('')
  })

  it('prefers the athlete\'s own time-of-day wording', () => {
    expect(sessionLabel({ date: '2026-08-04', slot: 0, timeOfDay: 'am' }, 2)).toBe('AM')
    expect(sessionLabel({ date: '2026-08-04', slot: 1, timeOfDay: 'evening' }, 2)).toBe(
      'evening'
    )
  })

  it('falls back to an explicit position when no time-of-day is set', () => {
    expect(sessionLabel({ date: '2026-08-04', slot: 1 }, 2)).toBe('2/2')
  })
})
