import { describe, expect, it } from 'vitest'
import { weekdayKey, withoutOwnWeekday } from './planTitle'

// 2026-10-31 is a Saturday.
const SATURDAY = '2026-10-31'

describe('withoutOwnWeekday (ai-trainer-ops#324)', () => {
  it('drops the day the chip already shows', () => {
    expect(withoutOwnWeekday('Saturday Easy Run', SATURDAY)).toBe('Easy Run')
    expect(withoutOwnWeekday('Samstag: lockerer Lauf', SATURDAY)).toBe('Lockerer Lauf')
    expect(withoutOwnWeekday('saturday - long ride', SATURDAY)).toBe('Long ride')
  })

  it('keeps a title that names another day, or only a day', () => {
    expect(withoutOwnWeekday('Sunday Long Ride', SATURDAY)).toBe('Sunday Long Ride')
    expect(withoutOwnWeekday('Saturday', SATURDAY)).toBe('Saturday')
    expect(withoutOwnWeekday("Saturday's Long Ride", SATURDAY)).toBe("Saturday's Long Ride")
    expect(withoutOwnWeekday('Saturdays Club Ride', SATURDAY)).toBe('Saturdays Club Ride')
  })

  it('names the weekday as the profile does', () => {
    expect(weekdayKey(SATURDAY)).toBe('saturday')
    expect(weekdayKey('2026-11-02')).toBe('monday')
  })
})
