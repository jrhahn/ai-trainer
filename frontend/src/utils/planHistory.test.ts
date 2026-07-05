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

  it('describes a removed day', () => {
    expect(summarizeDayChange({ workoutType: 'intervals', title: 'VO2max' }, null)).toBe(
      'Removed intervals — VO2max',
    )
  })

  it('falls back to "workout" when an added/removed day has no type or title', () => {
    expect(summarizeDayChange(null, {})).toBe('Added workout')
    expect(summarizeDayChange({}, null)).toBe('Removed workout')
  })

  it('returns "No change" when both days are null', () => {
    expect(summarizeDayChange(null, null)).toBe('No change')
  })

  it('describes changed fields', () => {
    const summary = summarizeDayChange(
      { workoutType: 'endurance', durationMinutes: 90 },
      { workoutType: 'recovery', durationMinutes: 45 },
    )
    expect(summary).toContain('type endurance → recovery')
    expect(summary).toContain('duration 90 → 45 min')
  })

  it('describes a title change', () => {
    expect(
      summarizeDayChange({ title: 'Old title' }, { title: 'New title' }),
    ).toBe('title “Old title” → “New title”')
  })

  it('renders em-dash placeholders for missing fields on either side', () => {
    const summary = summarizeDayChange(
      { workoutType: 'endurance', title: 'A', durationMinutes: 60 },
      {},
    )
    expect(summary).toContain('type endurance → —')
    expect(summary).toContain('title “A” → “—”')
    expect(summary).toContain('duration 60 → — min')
  })

  it('falls back to "Minor adjustment" when the compared fields are unchanged', () => {
    const day = { workoutType: 'endurance' as const, title: 'A', durationMinutes: 60 }
    expect(summarizeDayChange(day, { ...day })).toBe('Minor adjustment')
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
