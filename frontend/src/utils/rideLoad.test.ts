import { describe, expect, it } from 'vitest'
import { rideLoad } from './rideLoad'
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
