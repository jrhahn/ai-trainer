import type { Translate } from '../i18n'

/**
 * The headings the coach files its notes under, and the race calendar the app
 * merges in (backend `coach_memory.HEADINGS`, which a test holds this list to).
 * The writer puts them in English whatever language the notes are in.
 */
export const MEMORY_HEADINGS = [
  'Schedule constraints',
  'Fatigue & intensity response',
  'Hydration, fueling & heat response',
  'Preferred workout types',
  'Recurring issues',
  'Psychological training tendencies',
  'FTP & target context',
  'Race & event priorities',
  'Goals & motivations',
  'Race calendar',
] as const

// Read as the backend reads them (`coach_memory._normalised`).
const normalised = (text: string) => text.toLowerCase().replaceAll('&', 'and')

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// A heading opens a line after any list, number or emphasis marks, as in the
// backend's `_HEADING`, and is all the line says before a colon or its end:
// "Recurring issues with the knee" is a note, not a heading.
const HEADING = new RegExp(
  String.raw`^([ \t#*>\-\d.)]*)(` +
    MEMORY_HEADINGS.map((heading) => escaped(heading).replace('&', '(?:&|and)')).join('|') +
    String.raw`)(?=[ \t*_]*(?::|$))`,
  'gim',
)

/**
 * The coach's notes with each heading in the interface language
 * (ai-trainer-ops#258): a German athlete read "Schedule constraints:" above
 * their own words. Only the heading's name changes; its marks, and every other
 * line, stay as written.
 */
export function shownMemory(notes: string, t: Translate): string {
  return notes.replace(HEADING, (_, marks: string, name: string) => {
    const heading = MEMORY_HEADINGS.find((known) => normalised(known) === normalised(name))!
    return `${marks}${t(heading)}`
  })
}
