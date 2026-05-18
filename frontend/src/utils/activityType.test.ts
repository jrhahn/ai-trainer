import { describe, expect, it } from 'vitest'

import { activityNoun, formatActivityType, isCyclingActivity } from './activityType'

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

  it('returns an activity-specific noun for common Strava types', () => {
    expect(activityNoun('Hike')).toBe('hike')
    expect(activityNoun('TrailRun')).toBe('run')
    expect(activityNoun('WeightTraining')).toBe('strength session')
  })

  it('identifies cycling activity types', () => {
    expect(isCyclingActivity('Ride')).toBe(true)
    expect(isCyclingActivity('VirtualRide')).toBe(true)
    expect(isCyclingActivity('Hike')).toBe(false)
  })
})
