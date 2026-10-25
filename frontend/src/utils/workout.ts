import type { Language } from '../i18n'

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

export function formatDuration(seconds: number | undefined, language: Language = 'en'): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  // "1 h 30 min" in German, "1h 30m" in English (ai-trainer-ops#121).
  if (h > 0) return language === 'de' ? `${h} h ${m} min` : `${h}h ${m}m`
  return `${m} min`
}
