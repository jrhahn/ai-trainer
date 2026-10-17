import { describe, expect, it } from 'vitest'
import {
  describeEntry,
  groupEntriesByDate,
  sourceLabel,
  summarizeDayChange,
} from './planHistory'
import type { PlanDayHistoryEntry } from '../services/user'
import { translate, type Translate } from '../i18n'
import { planUpdateEvents } from './coachTimeline'

describe('sourceLabel', () => {
  it('maps known trigger keys to friendly labels', () => {
    expect(sourceLabel('coach_chat')).toBe('Coach chat')
    expect(sourceLabel('ride_review')).toBe('Post-ride adaptation')
    expect(sourceLabel('activity_import')).toBe('Activity synced')
    // Resolving an ambiguous match ticks the session; the history has to say it
    // was the athlete's own answer, not another automatic sync (#574).
    expect(sourceLabel('manual_match')).toBe('Match you confirmed')
    // Logging a session ticks it too, under its own trigger (ai-trainer-ops#111).
    expect(sourceLabel('workout_log')).toBe('Session you logged')
  })

  it('names a logged session in German', () => {
    const t: Translate = (key, values) => translate('de', key, values)
    expect(sourceLabel('workout_log', t)).toBe('Von dir eingetragene Einheit')
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

  it('reports when a day is marked complete', () => {
    const day = { workoutType: 'endurance' as const, title: 'A', durationMinutes: 60 }
    expect(summarizeDayChange(day, { ...day, completed: true })).toBe('marked complete')
  })
})

describe('describeEntry', () => {
  const base: PlanDayHistoryEntry = {
    id: 'h1',
    date: '2026-05-01',
    source: 'auto_adapt',
    applied: true,
    recordedAt: '2026-05-01T10:00:00Z',
    batchId: 'b1',
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

describe('groupEntriesByDate', () => {
  const entry = (id: string, date: string): PlanDayHistoryEntry => ({
    id,
    date,
    source: 'user_edit',
    applied: true,
    recordedAt: `${date}T10:00:00Z`,
    batchId: `batch-${id}`,
    oldDay: null,
    newDay: { workoutType: 'endurance' },
  })

  it('groups entries by training day, latest day first, preserving entry order', () => {
    const groups = groupEntriesByDate([
      entry('a', '2026-05-02'),
      entry('b', '2026-05-01'),
      entry('c', '2026-05-02'),
    ])

    expect(groups.map((g) => g.date)).toEqual(['2026-05-02', '2026-05-01'])
    expect(groups[0].entries.map((e) => e.id)).toEqual(['a', 'c'])
    expect(groups[1].entries.map((e) => e.id)).toEqual(['b'])
  })

  it('returns an empty array for no entries', () => {
    expect(groupEntriesByDate([])).toEqual([])
  })
})

describe('in German (ai-trainer-ops#103 Q27)', () => {
  const t: Translate = (text, vars) => translate('de', text, vars)

  it('names the change and keeps the nouns capitalised', () => {
    const entry = {
      id: 'h1',
      date: '2026-10-16',
      source: 'nightly_maintenance',
      applied: false,
      recordedAt: '2026-10-16T00:00:00Z',
      oldDay: { workoutType: 'rest', title: 'Rest' },
      newDay: { workoutType: 'endurance', title: 'Rest' },
    } as PlanDayHistoryEntry

    expect(describeEntry(entry, 'de')).toBe(
      'Nächtliche Feinabstimmung wollte ändern: Art Ruhetag → Ausdauer – deine Version bleibt',
    )
  })

  it('counts a batch in German', () => {
    const day = (date: string) => ({
      id: date,
      date,
      source: 'generate',
      applied: true,
      recordedAt: '2026-10-16T00:00:00Z',
      batchId: 'b1',
      oldDay: null,
      newDay: { workoutType: 'rest', title: 'Rest' },
    }) as unknown as PlanDayHistoryEntry

    const [event] = planUpdateEvents([day('2026-10-16'), day('2026-10-17')], t, 'de-DE')

    expect(event.title).toBe('Planerstellung')
    expect(event.body).toBe('2 Tage aktualisiert (2 hinzugefügt)')
  })
})
