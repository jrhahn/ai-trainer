import { describe, expect, it } from 'vitest'
import {
  effectivePlannedMinutes,
  formatPlanDuration,
  planDurationRange,
} from './planDuration'

describe('planDurationRange', () => {
  it('reads a single value as a degenerate window', () => {
    expect(planDurationRange({ durationMinutes: 90 })).toEqual([90, 90])
  })

  it('orders explicit bounds and fills a missing one from the scalar', () => {
    expect(planDurationRange({ durationMinMinutes: 180, durationMaxMinutes: 150 })).toEqual([150, 180])
    expect(planDurationRange({ durationMinutes: 120, durationMaxMinutes: 150 })).toEqual([120, 150])
  })

  it('returns null for a rest day', () => {
    expect(planDurationRange({ durationMinutes: 0 })).toBeNull()
    expect(planDurationRange(null)).toBeNull()
  })
})

describe('formatPlanDuration', () => {
  it('formats single values', () => {
    expect(formatPlanDuration({ durationMinutes: 180 })).toBe('3h')
    expect(formatPlanDuration({ durationMinutes: 45 })).toBe('45 min')
    expect(formatPlanDuration({ durationMinutes: 90 })).toBe('1h 30m')
  })

  it('formats an hour window as "2.5–3h"', () => {
    expect(formatPlanDuration({ durationMinMinutes: 150, durationMaxMinutes: 180 })).toBe('2.5–3h')
  })

  it('formats a sub-hour window in minutes', () => {
    expect(formatPlanDuration({ durationMinMinutes: 40, durationMaxMinutes: 50 })).toBe('40–50 min')
  })

  it('is empty when there is no duration', () => {
    expect(formatPlanDuration({ durationMinutes: 0 })).toBe('')
  })
})

describe('effectivePlannedMinutes', () => {
  const windowDay = { durationMinMinutes: 150, durationMaxMinutes: 180, durationMinutes: 165 }

  it('treats an in-window actual as on-target (returns the actual minutes)', () => {
    expect(effectivePlannedMinutes(windowDay, 165 * 60)).toBe(165)
    expect(effectivePlannedMinutes(windowDay, 150 * 60)).toBe(150)
  })

  it('snaps to the nearest bound outside the window', () => {
    expect(effectivePlannedMinutes(windowDay, 120 * 60)).toBe(150)
    expect(effectivePlannedMinutes(windowDay, 200 * 60)).toBe(180)
  })

  it('falls back to the scalar when there is no window', () => {
    expect(effectivePlannedMinutes({ durationMinutes: 90 }, 60 * 60)).toBe(90)
  })
})
