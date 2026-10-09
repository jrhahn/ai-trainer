import { describe, expect, it } from 'vitest'
import { enteredActivitySlot, isEnteredActivity } from './enteredActivity'
import type { RideMetricPoint } from '../store/useAppStore'

const ride = (fields: Partial<RideMetricPoint>): RideMetricPoint => ({
  stravaActivityId: 1, activityDate: '2026-10-01', sportType: 'running', ...fields,
})

describe('enteredActivitySlot (ai-trainer-ops#47)', () => {
  it.each([
    [{ activitySource: 'logged', externalActivityId: '2026-10-01#100' }, 100],
    [{ activitySource: 'logged', externalActivityId: '2026-10-01#0' }, null],
    [{ activitySource: 'logged', externalActivityId: null }, null],
    [{ activitySource: 'strava', externalActivityId: '2026-10-01#100' }, null],
  ])('%j → %j', (fields, expected) => {
    expect(enteredActivitySlot(ride(fields))).toBe(expected)
    expect(isEnteredActivity(ride(fields))).toBe(expected != null)
  })
})
