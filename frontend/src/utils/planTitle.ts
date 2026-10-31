import { parseLocalDate } from './workout'

/** The weekday names a plan title may open with, English and German, Sunday first
 *  as `Date.getDay()` counts. */
const WEEKDAY_NAMES = [
  ['sunday', 'sonntag'],
  ['monday', 'montag'],
  ['tuesday', 'dienstag'],
  ['wednesday', 'mittwoch'],
  ['thursday', 'donnerstag'],
  ['friday', 'freitag'],
  ['saturday', 'samstag'],
]

/** The weekday a plan date falls on, as the profile's training days name it. */
export function weekdayKey(date: string): string {
  return WEEKDAY_NAMES[parseLocalDate(date).getDay()][0]
}

/** ``title`` without a leading name of its own day's weekday.
 *
 * The plan writer often opens a title with the day ("Saturday Easy Run"). The
 * week strip already prints the weekday above it, and in a column of about
 * 44 px on a phone the repeated word was the one broken mid-word, "Saturda / y"
 * (ai-trainer-ops#324). A title that names another day keeps it: that day is
 * information.
 */
export function withoutOwnWeekday(title: string, date: string): string {
  const match = title.match(/^(\p{L}+)[\s:,–-]+(.+)$/u)
  if (!match || !WEEKDAY_NAMES[parseLocalDate(date).getDay()].includes(match[1].toLowerCase())) {
    return title
  }
  const rest = match[2].trim()
  return rest.charAt(0).toUpperCase() + rest.slice(1)
}
