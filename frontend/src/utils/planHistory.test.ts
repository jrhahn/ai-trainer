import { describe, expect, it } from 'vitest'
import { describeEntry, sourceLabel, summarizeDayChange } from './planHistory'
import type { PlanDayHistoryEntry } from '../services/user'

describe('sourceLabel', () => {
  it('maps known trigger keys to friendly labels', () => {
    expect(sourceLabel('coach_chat')).toBe('Coach chat')
    expect(sourceLabel('ride_review')).toBe('Post-ride adaptation')
  })

  it('title-cases unknown keys as a fallback', () => {
    expect(sourceLabel('some_new_trigger')).toBe('Some New Trigger')
  })
})

describe('summarizeDayChange', () => {
  it('describes an added day', () => {
    expect(summarizeDayChange(null, { workoutType: 'intervals', title: 'VO2max' })).toBe(
      'Added intervals — VO2max',
    )
  })

  it('describes changed fields', () => {
    const summary = summarizeDayChange(
      { workoutType: 'endurance', durationMinutes: 90 },
      { workoutType: 'recovery', durationMinutes: 45 },
    )
    expect(summary).toContain('type endurance → recovery')
    expect(summary).toContain('duration 90 → 45 min')
  })
})

describe('describeEntry', () => {
  const base: PlanDayHistoryEntry = {
    id: 'h1',
    date: '2026-05-01',
    source: 'auto_adapt',
    applied: true,
    recordedAt: '2026-05-01T10:00:00Z',
    oldDay: { workoutType: 'endurance' },
    newDay: { workoutType: 'recovery' },
  }

  it('frames an applied change plainly', () => {
    expect(describeEntry(base)).toBe('Auto-adaptation: type endurance → recovery')
  })

  it('frames a blocked change as kept-your-version', () => {
    expect(describeEntry({ ...base, applied: false })).toBe(
      'Auto-adaptation wanted to type endurance → recovery but kept your version',
    )
  })
})
