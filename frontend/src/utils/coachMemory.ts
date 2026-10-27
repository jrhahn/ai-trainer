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

// The race calendar's lines are the app's own English, one per race (backend
// `routers.users.races`): "- 2026-08-30 at 07:00: 120 km, 2000 m climbing".
// A list line that starts with a date goes through `t`. Its German pattern for
// exactly that sentence (de.ts, `DE_PATTERNS`, held to the backend by
// patterns.test.ts) words it, and any other such line comes back as it was.
const DATED_LINE = /^([ \t]*-[ \t]+)(\d{4}-\d{2}-\d{2}\b.*?)[ \t]*$/gm

/**
 * The coach's notes with each heading in the interface language
 * (ai-trainer-ops#258): a German athlete read "Schedule constraints:" above
 * their own words. Only the heading's name changes, and the race calendar's
 * lines the app wrote; the marks, and every other line, stay as written.
 */
export function shownMemory(notes: string, t: Translate): string {
  return notes
    .replace(HEADING, (_, marks: string, name: string) => {
      const heading = MEMORY_HEADINGS.find((known) => normalised(known) === normalised(name))!
      return `${marks}${t(heading)}`
    })
    .replace(DATED_LINE, (_, marks: string, line: string) => `${marks}${t(line)}`)
}
