/**
 * Parses a YYYY-MM-DD date string at noon local time so date arithmetic
 * is immune to timezone-offset-by-one errors.
 */
export function parseLocalDate(dateStr: string): Date {
  return new Date(dateStr + 'T12:00:00')
}

export function formatLocalDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
