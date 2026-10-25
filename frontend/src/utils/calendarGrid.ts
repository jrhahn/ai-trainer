import type { RaceEvent, RideMetricPoint, TrainingDay } from '../types/domain'
import { localeTag, type Language } from '../i18n'
import { sessionsForDate } from './planSessions'

/** One day of the month calendar. */
export interface CalendarCell {
  date: string
  /** Every session planned for the date, AM→PM; empty when nothing is planned. */
  sessions: TrainingDay[]
  events: RaceEvent[]
  rides: RideMetricPoint[]
}

export function formatIsoDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function formatDistance(value: number, language: Language): string {
  // No thousands separator, as before: a race reads "1000 km", not "1,000 km".
  return value.toLocaleString(localeTag(language), { maximumFractionDigits: 1, useGrouping: false })
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1, 12)
}

export function addMonths(date: Date, amount: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1, 12)
}

/** Six weeks, Monday first, from the week that holds the 1st of `month`. */
export function calendarWeeks(
  month: Date,
  plan: TrainingDay[],
  racesByDate: Record<string, RaceEvent[]>,
  ridesByDate: Record<string, RideMetricPoint[]>
): CalendarCell[][] {
  const firstCalendarDate = startOfMonth(month)
  const dayOfWeek = firstCalendarDate.getDay()
  const monday = new Date(firstCalendarDate)
  monday.setDate(firstCalendarDate.getDate() - ((dayOfWeek + 6) % 7))

  const result: CalendarCell[][] = []
  for (let w = 0; w < 6; w++) {
    const week: CalendarCell[] = []
    for (let d = 0; d < 7; d++) {
      const date = new Date(monday)
      date.setDate(monday.getDate() + w * 7 + d)
      const iso = formatIsoDate(date)
      week.push({
        date: iso,
        // A date may hold a two-a-day; the old `.find()` rendered only the
        // first session and silently hid the other (#496).
        sessions: sessionsForDate(plan, iso),
        events: racesByDate[iso] ?? [],
        rides: ridesByDate[iso] ?? [],
      })
    }
    result.push(week)
  }
  return result
}
