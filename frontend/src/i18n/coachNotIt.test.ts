import { describe, expect, it } from 'vitest'
import { DE } from './de'

// The coach is "your coach", never "it" (ai-trainer-ops#168). Every English
// interface string is a key of the German dictionary, so the keys are the
// English copy. A sentence that names the coach must not go on to call it "it"
// or "its": write it without a pronoun ("your coach updates the memory").
// "it" for something else in the sentence (a trait, a session) stays allowed.
const COACH_AS_IT = [
  /\bcoach\b[^.?!—]*\b(its|itself)\b/i,
  /\bcoach\b[^.?!—]*\bit (will|has|can|is|needs|learns|notes|suggests|plans|writes|answers|decides)\b/i,
  /\bcoach\b[^.?!—]*\b(before|until|when|and) it\b/i,
  /\bwrite to it\b/i,
]

describe('the coach is not "it"', () => {
  it('names the coach without "it" or "its" in the English copy', () => {
    const offending = Object.keys(DE).filter((key) => COACH_AS_IT.some((rule) => rule.test(key)))
    expect(offending).toEqual([])
  })

  it('catches the sentences it was written for', () => {
    for (const old of [
      'The coach will forget everything it has learned about you.',
      'As you chat with the coach, it will note patterns here.',
      'the coach updates its memory after each chat.',
      'Your coach needs a model before it can write a plan.',
      'The coach answers in the language you write to it.',
    ]) {
      expect(COACH_AS_IT.some((rule) => rule.test(old)), old).toBe(true)
    }
    expect(COACH_AS_IT.some((rule) => rule.test('Delete this trait? The coach will forget it.'))).toBe(false)
  })
})
