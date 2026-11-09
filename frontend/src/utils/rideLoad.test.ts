import { describe, expect, it } from 'vitest'
import { LOAD_MIN_RIDES, rideLoad, rideLoadLevel } from './rideLoad'
import type { RideMetricPoint } from '../store/useAppStore'

const ride = (fields: Partial<RideMetricPoint>): RideMetricPoint => ({
  stravaActivityId: 1,
  activityDate: '2026-10-27',
  sportType: 'Ride',
  ...fields,
})

describe('rideLoad (ai-trainer-ops#311)', () => {
  it.each(['provider', 'power', 'pace'])('counts a load from %s as measured', (source) => {
    expect(rideLoad(ride({ tss: 84.6, tssSource: source }))).toEqual({ value: 85, estimatedFrom: null })
  })

  it('counts a load with no source as measured, as the coach reads it', () => {
    expect(rideLoad(ride({ tss: 60, tssSource: null }))).toEqual({ value: 60, estimatedFrom: null })
  })

  it.each([
    ['heart_rate', 'estimated from your heart rate'],
    ['rpe', 'estimated from the effort you logged'],
    ['duration', 'estimated from the duration only'],
  ])('says how a load from %s was estimated', (source, how) => {
    expect(rideLoad(ride({ tss: 45.2, tssSource: source }))).toEqual({ value: 45, estimatedFrom: how })
  })

  it('still calls a load from a source it does not know an estimate', () => {
    expect(rideLoad(ride({ tss: 30, tssSource: 'something_new' }))?.estimatedFrom).toBe('estimated')
  })

  it('has nothing for a ride without a load', () => {
    expect(rideLoad(ride({}))).toBeNull()
    expect(rideLoad(ride({ tss: Number.NaN }))).toBeNull()
  })
})

describe('rideLoadLevel (ai-trainer-ops#313)', () => {
  // Six rides of loads 10..60 on 2026-10-27, so each one sees all six.
  const week = [10, 20, 30, 40, 50, 60].map((tss, i) => ride({ stravaActivityId: 100 + i, tss }))

  it('calls the lightest third light, the middle moderate and the hardest third high', () => {
    expect(week.map((r) => rideLoadLevel(r, week))).toEqual(['easy', 'easy', 'moderate', 'moderate', 'hard', 'hard'])
  })

  it('compares with the six weeks up to the ride, not with later rides', () => {
    const later = ride({ stravaActivityId: 200, tss: 500, activityDate: '2026-11-01' })
    const before = ride({ stravaActivityId: 201, tss: 500, activityDate: '2026-09-14' })
    const earlier = [10, 20, 30, 40, 50].map((tss, i) =>
      ride({ stravaActivityId: 110 + i, tss, activityDate: `2026-10-${21 + i}` }),
    )
    const today = ride({ stravaActivityId: 106, tss: 60, activityDate: '2026-10-27' })
    expect(rideLoadLevel(today, [...earlier, today, later, before])).toBe('hard')
    // The ride on the 21st has only itself to go by: nothing yet.
    expect(rideLoadLevel(earlier[0], [...earlier, today])).toBeNull()
  })

  it('only compares a run with runs', () => {
    const runs = week.map((r) => ({ ...r, sportType: 'Run', tss: 5 }))
    const run = ride({ stravaActivityId: 300, sportType: 'Run', tss: 80, activityDate: '2026-10-27' })
    expect(rideLoadLevel(run, [...week, ...runs, run])).toBe('hard')
    expect(rideLoadLevel(week[0], [...week, ...runs])).toBe('easy')
  })

  it('says nothing until there are enough rides to compare with', () => {
    const few = week.slice(0, LOAD_MIN_RIDES - 1)
    expect(rideLoadLevel(few[0], few)).toBeNull()
  })

  it('counts a ride listed twice once', () => {
    const few = week.slice(0, LOAD_MIN_RIDES - 1)
    expect(rideLoadLevel(few[0], [...few, few[1]])).toBeNull()
  })

  it('calls a run of equal rides moderate', () => {
    const same = week.map((r) => ({ ...r, tss: 40 }))
    expect(same.map((r) => rideLoadLevel(r, same))).toEqual(Array(6).fill('moderate'))
  })

  it('has no word for a ride without a load', () => {
    expect(rideLoadLevel(ride({}), week)).toBeNull()
  })
})
