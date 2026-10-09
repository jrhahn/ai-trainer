/**
 * The #710 rule, now that it can be stated rather than inferred from a glyph.
 *
 * It was checked by searching a calendar cell's `textContent` for "🏃". That
 * worked while the glyph was the implementation, and ai-trainer-ops#51.7 made
 * it not be — an icon leaves nothing in the text, so the old assertions would
 * have been equally satisfied by a cell that drew nothing whatsoever.
 */

import { Bike, Dumbbell, Footprints, Gauge, Moon, Timer, Zap } from 'lucide-react'
import { describe, expect, it } from 'vitest'

import { UNKNOWN_SPORT_ICON, sessionIcon } from './sessionIcon'
import { sessionTypeStyle } from './sessionType'
import type { TrainingDay } from '../store/useAppStore'

function day(workoutType: TrainingDay['workoutType'], sport?: string): TrainingDay {
  return {
    date: '2026-05-01',
    workoutType,
    title: 'A session',
    description: '',
    durationMinutes: 60,
    ...(sport ? { sport } : {}),
  } as TrainingDay
}

describe('sessionIcon', () => {
  it('draws the sport, not the type, when the type names a sport', () => {
    // The bug: `endurance` and `strength` are sports in disguise, so a planned
    // run was shown a bicycle.
    expect(sessionIcon(day('endurance', 'running'))).toBe(Footprints)
    expect(sessionIcon(day('endurance', 'running'))).not.toBe(Bike)
    expect(sessionIcon(day('strength', 'running'))).toBe(Footprints)
  })

  it('still draws a bicycle for a cycling day', () => {
    expect(sessionIcon(day('endurance', 'cycling'))).toBe(Bike)
    // And when nothing says otherwise: cycling is the planner's default.
    expect(sessionIcon(day('endurance'))).toBe(Bike)
  })

  it('draws a dumbbell for a strength session in the gym', () => {
    expect(sessionIcon(day('strength', 'strength'))).toBe(Dumbbell)
  })

  it('draws a stopwatch, not a bicycle, for a sport it has no icon for', () => {
    // A sport outside the three the planner writes still reaches storage: the
    // persist gate keeps an athlete's own edit rather than relabelling it.
    const swim = sessionIcon(day('endurance', 'swim'))
    expect(swim).toBe(UNKNOWN_SPORT_ICON)
    expect(swim).toBe(Timer)
    expect(swim).not.toBe(Bike)
  })

  it('keeps the type for a purpose that means the same in every sport', () => {
    // A run with intervals is still intervals.
    expect(sessionIcon(day('intervals', 'running'))).toBe(Zap)
    expect(sessionIcon(day('tempo', 'running'))).toBe(Gauge)
    expect(sessionIcon(day('rest', 'running'))).toBe(Moon)
  })

  it('takes its type icons from the shared table', () => {
    // The calendar used to keep a second table, so the same Thursday could be a
    // flame here and a gauge in the week strip. Asserting that one icon comes
    // from `sessionTypeStyle` pins the two together; asserting all of them
    // would just restate that table.
    expect(sessionIcon(day('race', 'running'))).toBe(sessionTypeStyle('race').Icon)
  })
})
