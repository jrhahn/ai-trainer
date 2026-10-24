import { describe, expect, it } from 'vitest'

import { activityNoun } from './activityType'

describe('activityNoun', () => {
  it('returns an activity-specific noun for common Strava types', () => {
    expect(activityNoun('Hike')).toBe('hike')
    expect(activityNoun('TrailRun')).toBe('run')
    expect(activityNoun('WeightTraining')).toBe('strength session')
  })
})
