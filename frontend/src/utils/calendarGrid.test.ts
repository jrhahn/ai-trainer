import { describe, expect, it } from 'vitest'
import type { TrainingDay } from '../store/useAppStore'
import { addMonths, calendarWeeks, formatDistance, startOfMonth } from './calendarGrid'

describe('calendarWeeks (ai-trainer-ops#274)', () => {
  it('lays out six weeks from the Monday of the week that holds the 1st', () => {
    // 1 October 2026 is a Thursday.
    const weeks = calendarWeeks(new Date(2026, 9, 15, 12), [], {}, {})

    expect(weeks).toHaveLength(6)
    expect(weeks.every((week) => week.length === 7)).toBe(true)
    expect(weeks[0][0].date).toBe('2026-09-28')
    expect(weeks[0][3].date).toBe('2026-10-01')
    expect(weeks[5][6].date).toBe('2026-11-08')
  })

  it('puts both sessions of a two-a-day, the races and the rides on their day', () => {
    const plan = [
      { date: '2026-10-06', slot: 0, workoutType: 'endurance', title: 'AM' },
      { date: '2026-10-06', slot: 1, workoutType: 'strength', title: 'PM' },
    ] as TrainingDay[]
    const race = { id: 'r1', date: '2026-10-06', distanceKm: 100, elevationM: 1200 }

    const cell = calendarWeeks(new Date(2026, 9, 1, 12), plan, { '2026-10-06': [race] }, {})[1][1]

    expect(cell.date).toBe('2026-10-06')
    expect(cell.sessions.map((s) => s.title)).toEqual(['AM', 'PM'])
    expect(cell.events).toEqual([race])
    expect(cell.rides).toEqual([])
  })

  it('moves by whole months from the 1st, at noon', () => {
    expect(addMonths(startOfMonth(new Date(2026, 0, 31)), 1)).toEqual(new Date(2026, 1, 1, 12))
  })

  it('writes a race distance without a thousands separator', () => {
    expect(formatDistance(1000, 'en')).toBe('1000')
    expect(formatDistance(42.195, 'de')).toBe('42,2')
  })
})
