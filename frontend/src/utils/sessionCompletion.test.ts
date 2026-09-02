import { describe, expect, it } from 'vitest'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { sessionKey } from './planSessions'
import { loggedSessionKeys, nextUnfinishedSession } from './sessionCompletion'

const DATE = '2026-09-02'

const session = (overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date: DATE,
  workoutType: 'endurance',
  title: 'Session',
  description: '',
  durationMinutes: 60,
  ...overrides,
})

const ride = (overrides: Partial<RideMetricPoint> = {}): RideMetricPoint =>
  ({
    activityDate: DATE,
    sportType: 'Ride',
    ...overrides,
  }) as RideMetricPoint

const AM = session({ slot: 0, title: 'Morning' })
const PM = session({ slot: 1, title: 'Evening' })

describe('loggedSessionKeys', () => {
  it('takes the completed flag at face value', () => {
    const done = loggedSessionKeys([session({ completed: true })], [], DATE)

    expect(done.has(sessionKey(AM))).toBe(true)
  })

  it('counts a ride on a single-session day even when nothing was ticked', () => {
    // The #472/#473 fallback: ridden but never hand-ticked.
    const done = loggedSessionKeys([session({ completed: false })], [ride()], DATE)

    expect(done.size).toBe(1)
  })

  it('does not let one ride tick off both halves of a two-a-day', () => {
    // The whole reason this is per session rather than per date (#645).
    const done = loggedSessionKeys([AM, PM], [ride()], DATE)

    expect(done.size).toBe(0)
  })

  it('attributes a matched ride to the session it matched', () => {
    const done = loggedSessionKeys(
      [AM, PM],
      [ride({ matchedPlanDate: DATE, matchedPlanSnapshot: { date: DATE, slot: 1 } })],
      DATE
    )

    expect(done.has(sessionKey(PM))).toBe(true)
    expect(done.has(sessionKey(AM))).toBe(false)
  })

  it('ignores a ride matched to a different date', () => {
    const done = loggedSessionKeys(
      [AM, PM],
      [ride({ matchedPlanDate: '2026-09-01', matchedPlanSnapshot: { date: '2026-09-01' } })],
      DATE
    )

    expect(done.size).toBe(0)
  })

  it('treats a snapshot without a slot as the first session', () => {
    // Legacy single-session days carry no slot and read back as slot 0.
    const done = loggedSessionKeys(
      [AM, PM],
      [ride({ matchedPlanDate: DATE, matchedPlanSnapshot: { date: DATE } })],
      DATE
    )

    expect(done.has(sessionKey(AM))).toBe(true)
    expect(done.has(sessionKey(PM))).toBe(false)
  })

  it('ignores rides from other days', () => {
    const done = loggedSessionKeys([session()], [ride({ activityDate: '2026-08-30' })], DATE)

    expect(done.size).toBe(0)
  })

  it('survives an empty plan', () => {
    expect(loggedSessionKeys([], [ride()], DATE).size).toBe(0)
  })
})

describe('nextUnfinishedSession', () => {
  it('gives the coach the session still to be ridden', () => {
    const done = new Set([sessionKey(AM)])

    expect(nextUnfinishedSession([AM, PM], done)?.title).toBe('Evening')
  })

  it('gives the first when nothing is done yet', () => {
    expect(nextUnfinishedSession([AM, PM], new Set())?.title).toBe('Morning')
  })

  it('falls back to the last once the day is finished', () => {
    // A completed session is better context than none — the day is still what
    // the conversation is about.
    const done = new Set([sessionKey(AM), sessionKey(PM)])

    expect(nextUnfinishedSession([AM, PM], done)?.title).toBe('Evening')
  })

  it('is undefined on a day with no sessions', () => {
    expect(nextUnfinishedSession([], new Set())).toBeUndefined()
  })
})
