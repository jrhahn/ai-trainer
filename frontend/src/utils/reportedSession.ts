import type { RideMetricPoint } from '../store/useAppStore'
import { activityNoun } from './activityType'
import { isEnteredActivity } from './enteredActivity'
import { formatLocalDate } from './workout'

/**
 * A session the athlete reports in the chat, read off their own words
 * (ai-trainer-ops#166).
 *
 * "i did that. 30km on a road bike, 1h 200w np" was answered with praise and a
 * question about pacing, and nothing was recorded: without a device or a
 * provider the ride existed only in the chat. The chat now offers to enter it,
 * with the form prefilled from these words.
 *
 * Deliberately narrow, because it only *offers*: the athlete confirms in the
 * form (LogSessionDialog), and nothing is saved without that. It needs a word
 * that says the session happened ("did", "rode", "gefahren") and a duration or
 * a distance, and it stays quiet on anything about the future ("tomorrow",
 * "will", "should I"). A miss costs the athlete a tap on "Add activity"; a
 * false offer costs a "No thanks".
 */
export interface ReportedSession {
  date: string
  sport: 'cycling' | 'running' | 'strength'
  durationMinutes?: number
  distanceKm?: number
  normalizedPower?: number
  averageHeartRate?: number
}

const HAPPENED =
  /\b(did|done|rode|ridden|ran|finished|completed|went|had|i've|ive|gefahren|gemacht|gelaufen|absolviert|geschafft)\b/i
const FUTURE =
  /\b(tomorrow|will|going to|gonna|plan to|planning|should i|shall i|can i|(?<!(?:guten|heute)\s)morgen|übermorgen|werde|soll ich|kann ich|plane)\b/i
const RUN = /\b(run|ran|running|jog|jogged|jogging|lauf|laufen|gelaufen|joggen|gejoggt)\b/i
const STRENGTH = /\b(gym|strength|weights|lifting|kraft|krafttraining|fitnessstudio)\b/i
const YESTERDAY = /\b(yesterday|gestern)\b/i

const number = (text: string) => Number(text.replace(',', '.'))

function minutesOf(text: string): number | undefined {
  // "1:30 h" or "1h30" / "1 h 30 min"
  const clock = text.match(/\b(\d{1,2}):(\d{2})\s*(?:h|std)\b/i)
  if (clock) return Number(clock[1]) * 60 + Number(clock[2])
  // The minutes need their unit, or nothing that reads as another one: "2h 30
  // km" is two hours and a distance, not 150 minutes.
  const hoursAndMinutes = text.match(/\b(\d{1,2})\s*h\s*(\d{1,2})(?:\s*(?:min|m)\b|(?!\d|\s*[a-zäöü]))/i)
  if (hoursAndMinutes) return Number(hoursAndMinutes[1]) * 60 + Number(hoursAndMinutes[2])
  const hours = text.match(/\b(\d{1,2}(?:[.,]\d+)?)\s*(?:h|hr|hrs|hour|hours|std|stunde|stunden)\b/i)
  if (hours) return Math.round(number(hours[1]) * 60)
  const minutes = text.match(/\b(\d{1,3})\s*(?:min|mins|minute|minutes|minuten)\b/i)
  if (minutes) return Number(minutes[1])
  return undefined
}

function inRange(value: number | undefined, min: number, max: number): number | undefined {
  return value != null && Number.isFinite(value) && value >= min && value <= max ? value : undefined
}

export function parseReportedSession(text: string, today: Date = new Date()): ReportedSession | null {
  if (!HAPPENED.test(text) || FUTURE.test(text)) return null

  const durationMinutes = inRange(minutesOf(text), 1, 1440)
  const km = text.match(/\b(\d{1,4}(?:[.,]\d+)?)\s*(?:km|kilometers?|kilometres?|kilometer)\b/i)
  const distanceKm = inRange(km ? number(km[1]) : undefined, 0.1, 1000)
  if (durationMinutes == null && distanceKm == null) return null

  const sport = STRENGTH.test(text) ? 'strength' : RUN.test(text) ? 'running' : 'cycling'
  const np =
    text.match(/\b(\d{2,4})\s*w(?:atts?)?\s*(?:np|normali[sz]ed)\b/i) ??
    text.match(/\b(?:np|normali[sz]ed power)\s*(?:of\s*|von\s*)?(\d{2,4})\b/i)
  const hr =
    text.match(/\b(\d{2,3})\s*bpm\b/i) ??
    text.match(/\b(?:hr|heart rate|avg hr|puls|herzfrequenz|hf)\s*(?:of\s*|von\s*)?(\d{2,3})\b/i)

  const day = new Date(today)
  if (YESTERDAY.test(text)) day.setDate(day.getDate() - 1)

  const session: ReportedSession = { date: formatLocalDate(day), sport }
  if (durationMinutes != null) session.durationMinutes = durationMinutes
  if (distanceKm != null && sport !== 'strength') session.distanceKm = distanceKm
  const power = inRange(np ? Number(np[1]) : undefined, 1, 2500)
  if (power != null && sport === 'cycling') session.normalizedPower = power
  const heartRate = inRange(hr ? Number(hr[1]) : undefined, 30, 250)
  if (heartRate != null && sport !== 'strength') session.averageHeartRate = heartRate
  return session
}

const NOUN_TO_SPORT: Record<string, ReportedSession['sport']> = {
  ride: 'cycling',
  run: 'running',
  'strength session': 'strength',
}

/** Whether a device or a provider already brought this session in: then the
 *  chat does not offer to enter it twice. Entered activities do not count. */
export function alreadyRecorded(session: ReportedSession, rides: RideMetricPoint[]): boolean {
  return rides.some(
    (ride) =>
      ride.activityDate === session.date &&
      !isEnteredActivity(ride) &&
      NOUN_TO_SPORT[activityNoun(ride.sportType)] === session.sport
  )
}
