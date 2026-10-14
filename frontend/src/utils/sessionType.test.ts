/**
 * The icons the week strip needs (ai-trainer-ops#51.4, #90).
 *
 * What is checked here is the part a browser cannot catch: that a type added to
 * the union tomorrow comes with an icon of its own, rather than falling through
 * to the next release.
 */

import { describe, expect, it } from 'vitest'

import { SESSION_TYPES, fallbackSessionTypeStyle, sessionTypeStyle } from './sessionType'

describe('session type styles', () => {
  it('covers every type', () => {
    // Guards the iteration below: a `SESSION_TYPES` that had gone empty would
    // make every `for` loop here pass while testing nothing.
    expect(SESSION_TYPES.length).toBeGreaterThanOrEqual(7)
  })

  it('gives every type its own icon and a text colour to draw it in', () => {
    const icons = SESSION_TYPES.map((type) => sessionTypeStyle(type).Icon)
    expect(new Set(icons).size, 'two types drawn with the same icon').toBe(icons.length)

    for (const type of SESSION_TYPES) {
      const { iconColor } = sessionTypeStyle(type)
      // A `bg-` class here would paint a square instead of colouring the glyph,
      // which is the mistake the dot-to-icon change invites.
      expect(iconColor, `${type}'s icon colour`).toMatch(/^text-/)
    }
  })

  it('answers for a type the API has not told us about yet', () => {
    // Reachable: `workoutType` arrives from the model's JSON.
    const unknown = sessionTypeStyle('triathlon' as never)
    expect(unknown).toBe(fallbackSessionTypeStyle())
    expect(unknown.Icon).toBeTruthy()
  })
})
