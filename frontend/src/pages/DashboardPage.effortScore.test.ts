import { describe, expect, it } from 'vitest'
import { computeMatchScore, intervalTargetKind, parsePaceSecondsPerKm } from './DashboardPage'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'

// Heart-rate and pace intervals are scored against those targets (ai-trainer-ops#133).
// The same cases as backend/tests/test_hr_pace_compliance.py.

function ride(overrides: Partial<RideMetricPoint> = {}): RideMetricPoint {
  return {
    activityDate: '2026-10-20',
    sportType: 'Run',
    durationSeconds: 3600,
    ...overrides,
  } as RideMetricPoint
}

const HR_PLAN: Partial<TrainingDay> = {
  date: '2026-10-20',
  title: 'Threshold intervals by heart rate',
  workoutType: 'intervals',
  durationMinutes: 60,
  intervals: [
    { duration: 600, heartRate: 165, rest: 180 },
    { duration: 600, heartRate: 168, rest: 180 },
  ],
}

const PACE_PLAN: Partial<TrainingDay> = {
  date: '2026-10-21',
  title: 'Tempo run intervals',
  workoutType: 'intervals',
  durationMinutes: 50,
  intervals: [
    { duration: 480, pace: '4:30/km', rest: 120 },
    { duration: 480, pace: '4:20/km', rest: 120 },
  ],
}

describe('pace and target kind', () => {
  it('reads a pace as seconds per kilometre', () => {
    expect(parsePaceSecondsPerKm('4:30/km')).toBe(270)
    expect(parsePaceSecondsPerKm('4:30')).toBe(270)
    expect(parsePaceSecondsPerKm(' 5:05 / km ')).toBe(305)
    expect(parsePaceSecondsPerKm('4:75/km')).toBeNull()
    expect(parsePaceSecondsPerKm('4:30/mi')).toBeNull()
    expect(parsePaceSecondsPerKm(undefined)).toBeNull()
  })

  it('names what the intervals are set in', () => {
    expect(intervalTargetKind(HR_PLAN)).toBe('heart rate')
    expect(intervalTargetKind(PACE_PLAN)).toBe('pace')
    expect(intervalTargetKind({ ...HR_PLAN, intervals: [{ duration: 600, power: 280, rest: 60 }] })).toBe('power')
    expect(intervalTargetKind({ ...HR_PLAN, intervals: [] })).toBeNull()
  })
})

describe('computeMatchScore on heart-rate intervals', () => {
  it('scores a session held at the target high and an easy one low', () => {
    expect(computeMatchScore(ride({ avgHeartRateBpm: 166 }), HR_PLAN)).toBeGreaterThanOrEqual(90)
    expect(computeMatchScore(ride({ avgHeartRateBpm: 158 }), HR_PLAN)).toBeGreaterThanOrEqual(85)
    expect(computeMatchScore(ride({ avgHeartRateBpm: 120 }), HR_PLAN)).toBeLessThan(60)
  })

  it('does not score heart-rate intervals on watts the plan did not ask for', () => {
    const score = computeMatchScore(
      ride({ avgHeartRateBpm: 120, normalizedPowerW: 150, avgPowerW: 150, tss: 40 }),
      HR_PLAN
    )
    expect(score).toBeLessThan(60)
  })

  it('falls back to the clock without a heart rate', () => {
    expect(computeMatchScore(ride(), HR_PLAN)).toBe(100)
  })
})

describe('computeMatchScore on pace intervals', () => {
  it('scores a run that held its pace well above one that jogged', () => {
    const held = computeMatchScore(ride({ durationSeconds: 3000, distanceKm: 3000 / 290 }), PACE_PLAN)
    const jogged = computeMatchScore(ride({ durationSeconds: 3000, distanceKm: 3000 / 390 }), PACE_PLAN)
    expect(held).toBeGreaterThanOrEqual(85)
    expect(jogged).toBeLessThan(60)
    expect((held ?? 0) - (jogged ?? 0)).toBeGreaterThanOrEqual(30)
  })
})
