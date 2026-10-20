import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DE } from './de'

// The coach is "your coach", never "it" (ai-trainer-ops#168). The English copy
// is read twice: as the keys of the German dictionary, and as every `t('…')`
// literal in the source, so a string without a German entry is checked too.
// A sentence that names the coach must not go on to call it "it" or "its":
// write it without a pronoun ("your coach updates the memory").
// "it" for something else in the sentence (a trait, a session) stays allowed.
const COACH_AS_IT = [
  /\bcoach\b[^.?!—]*\b(its|itself)\b/i,
  /\bcoach\b[^.?!—]*\bit (will|has|can|is|should|would|may|might|must|did|\w+s)\b/i,
  /\bcoach\b[^.?!—]*\bit'(ll|s)\b/i,
  /\bcoach\b[^.?!—]*\b(before|until|when|and) it\b/i,
  /\bwrite to it\b/i,
]

function sourceStrings(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sourceStrings(path)
    if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) return []
    // Single or double quotes: the repo uses double ones when the text holds
    // an apostrophe ("can't"). Template literals are dynamic and left out.
    return [...readFileSync(path, 'utf8').matchAll(/\bt\(\s*(['"])((?:(?!\1)[^\\]|\\.)*)\1/g)].map((m) => m[2])
  })
}

describe('the coach is not "it"', () => {
  it('names the coach without "it" or "its" in the English copy', () => {
    const copy = [...Object.keys(DE), ...sourceStrings(resolve(__dirname, '..'))]
    const offending = [...new Set(copy)].filter((text) => COACH_AS_IT.some((rule) => rule.test(text)))
    expect(offending).toEqual([])
  })

  it('catches the sentences it was written for', () => {
    for (const old of [
      'The coach will forget everything it has learned about you.',
      'As you chat with the coach, it will note patterns here.',
      'the coach updates its memory after each chat.',
      'Your coach needs a model before it can write a plan.',
      'The coach answers in the language you write to it.',
      'This is what the coach believes, and it decides which sessions get recommended.',
      "Your coach can't tell yet, so it'll ask.",
      'The coach checks this before it should change the plan.',
    ]) {
      expect(COACH_AS_IT.some((rule) => rule.test(old)), old).toBe(true)
    }
    expect(COACH_AS_IT.some((rule) => rule.test('Delete this trait? The coach will forget it.'))).toBe(false)
  })
})
