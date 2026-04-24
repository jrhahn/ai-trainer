/**
 * Parses a YYYY-MM-DD date string at noon local time so date arithmetic
 * is immune to timezone-offset-by-one errors.
 */
export function parseLocalDate(dateStr: string): Date {
  return new Date(dateStr + 'T12:00:00')
}
