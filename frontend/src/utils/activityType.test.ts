import { describe, expect, it } from 'vitest'

import { formatActivityType } from './activityType'

describe('formatActivityType', () => {
  it('keeps plain ride labels readable', () => {
    expect(formatActivityType('Ride')).toBe('Ride')
  })

  it('removes the ride suffix from compound cycling types', () => {
    expect(formatActivityType('VirtualRide')).toBe('Virtual')
  })

  it('humanizes camel-cased non-ride activity types', () => {
    expect(formatActivityType('WeightTraining')).toBe('Weight training')
  })

  it('keeps simple non-ride activity types unchanged apart from casing', () => {
    expect(formatActivityType('Yoga')).toBe('Yoga')
  })
})
