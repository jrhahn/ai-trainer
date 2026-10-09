/**
 * The effort scale is named the same way everywhere (ai-trainer-ops#51.5).
 *
 * The scale was written out twice: `WorkoutFeedbackForm` had the strength
 * wording, `WorkoutPage` had a copy without it. So a strength session logged at
 * 2 was "Controlled" while choosing it and "Moderate" in the log that appeared
 * directly afterwards — the same number, named two things one screen apart.
 *
 * A test of the shared helper cannot by itself stop the next copy being made;
 * what it can do is pin the behaviour the copy got wrong, so a replacement that
 * forgets strength fails here rather than in front of a lifter.
 */

import { describe, expect, it } from 'vitest'

import { EFFORT_LEVELS, effortLabel, effortLabelsFor } from './effort'
import type { TrainingDay } from '../store/useAppStore'

const day = (workoutType: TrainingDay['workoutType']) => ({ workoutType })

describe('effort labels', () => {
  it('has five levels', () => {
    expect([...EFFORT_LEVELS]).toEqual([1, 2, 3, 4, 5])
  })

  it('names every level for every kind of session', () => {
    for (const workoutType of ['endurance', 'strength', 'rest'] as const) {
      for (const level of EFFORT_LEVELS) {
        expect(effortLabel(day(workoutType), level), `${workoutType} ${level}`).toBeTruthy()
      }
    }
  })

  it('words the middle of the scale differently for strength', () => {
    // The specific difference, not just "they differ": a strength session is
    // controlled or challenging, not moderate or hard.
    expect(effortLabel(day('strength'), 2)).toBe('Controlled')
    expect(effortLabel(day('strength'), 3)).toBe('Challenging')
    expect(effortLabel(day('endurance'), 2)).toBe('Moderate')
    expect(effortLabel(day('endurance'), 3)).toBe('Hard')
  })

  it('agrees with itself at the ends, where both scales mean the same thing', () => {
    for (const level of [1, 5] as const) {
      expect(effortLabel(day('strength'), level)).toBe(effortLabel(day('endurance'), level))
    }
  })

  it('treats everything that is not strength as a ride', () => {
    const ride = effortLabelsFor(day('endurance'))
    for (const workoutType of ['rest', 'recovery', 'tempo', 'intervals', 'race'] as const) {
      expect(effortLabelsFor(day(workoutType))).toEqual(ride)
    }
    // Including nothing at all: the log renders before the day is in hand on a
    // cold load, and a crash there would hide a session that was recorded fine.
    expect(effortLabelsFor(undefined)).toEqual(ride)
  })

  it('says something rather than nothing for a level off the scale', () => {
    // `perceivedEffort` comes back from the API as a number. A 6 from a future
    // schema should read as "6/5 (6)", which is odd, and not as "6/5
    // (undefined)", which looks like data loss.
    expect(effortLabel(day('endurance'), 6)).toBe('6')
    expect(effortLabel(day('endurance'), 0)).toBe('0')
  })
})
