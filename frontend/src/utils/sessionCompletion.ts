import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { sessionKey, sessionSlot } from './planSessions'

/**
 * Which of a date's sessions count as ridden (#645).
 *
 * Three rules, in order of how much they know:
 *
 * 1. `completed` on the session itself — the athlete ticked it, or the import
 *    marked it. Unambiguous.
 * 2. A ride matched to *that* session: same date and same slot. Ride matching
 *    records the session it chose in `matchedPlanSnapshot`, so a two-a-day can
 *    attribute its rides correctly.
 * 3. Any ride on the date, **only when the date holds a single session**. This
 *    is the #472/#473 fallback for a ride that was never hand-ticked and never
 *    matched. It is safe when there is nothing to confuse it with, and wrong the
 *    moment there are two sessions — one ride would mark both done.
 *
 * Rule 3 is the reason this is a function rather than a boolean prop. The
 * dashboard used to pass one `logged` flag for the whole date, which was correct
 * only because it never showed more than one session.
 */
export function loggedSessionKeys(
  sessions: TrainingDay[],
  rides: RideMetricPoint[],
  date: string
): Set<string> {
  const done = new Set<string>()
  if (sessions.length === 0) return done

  for (const session of sessions) {
    if (session.completed) done.add(sessionKey(session))
  }

  const ridesOnDate = rides.filter((ride) => ride.activityDate === date)
  if (ridesOnDate.length === 0) return done

  for (const ride of ridesOnDate) {
    if (ride.matchedPlanDate !== date) continue
    const snapshot = ride.matchedPlanSnapshot
    if (!snapshot) continue
    const slot = sessionSlot(snapshot as { date: string; slot?: number })
    const matched = sessions.find((session) => sessionSlot(session) === slot)
    if (matched) done.add(sessionKey(matched))
  }

  // Rule 3: only where a ride cannot be attributed to the wrong session.
  if (sessions.length === 1) done.add(sessionKey(sessions[0]))

  return done
}

/**
 * The session the coach should be given as context for *today*.
 *
 * The next one not yet done, so a two-a-day stops handing the coach the morning
 * session all afternoon. Falls back to the last session once everything is done
 * — the day is still what the conversation is about, and a finished session is
 * better context than none.
 */
export function nextUnfinishedSession(
  sessions: TrainingDay[],
  done: Set<string>
): TrainingDay | undefined {
  if (sessions.length === 0) return undefined
  return sessions.find((session) => !done.has(sessionKey(session))) ?? sessions[sessions.length - 1]
}
