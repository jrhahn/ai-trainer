/**
 * Per-day session helpers for the training plan (#496).
 *
 * A training plan is a flat list where a **date may repeat**: two-a-days are two
 * entries sharing one date, distinguished by `slot` (0 = first/AM, 1 = second/PM).
 * Every `plan.find((d) => d.date === iso)` therefore silently dropped the second
 * session — these helpers replace that pattern with an explicit list.
 *
 * A legacy single-workout day carries no `slot` and reads back as slot 0, so
 * nothing about existing plans changes.
 */

/** The minimal shape these helpers need — keeps them usable for snapshots too. */
export interface PlanSessionFields {
  date: string
  slot?: number
  timeOfDay?: string
}

/** A session's slot, defaulting to 0 for legacy days and any unusable value.
 *
 * Takes only the field it reads, so a `Partial<TrainingDay>` snapshot — which
 * carries an optional `date` — can be asked for its slot without a cast (#648).
 */
export function sessionSlot(
  day: { date?: string; slot?: number } | null | undefined
): number {
  const slot = day?.slot
  if (typeof slot !== 'number' || !Number.isFinite(slot)) return 0
  return Math.max(0, Math.trunc(slot))
}

/** Stable React key / identity for a session: `"2026-08-04#1"`. */
export function sessionKey(day: PlanSessionFields): string {
  return `${day.date}#${sessionSlot(day)}`
}

/** Whether two entries are the same session. */
export function isSameSession(
  a: PlanSessionFields | null | undefined,
  b: PlanSessionFields | null | undefined
): boolean {
  if (!a || !b) return false
  return a.date === b.date && sessionSlot(a) === sessionSlot(b)
}

/**
 * Every session planned for `date`, in slot order.
 *
 * Returns an empty array when the date has no planned session, so callers can
 * map unconditionally.
 */
export function sessionsForDate<T extends PlanSessionFields>(
  plan: T[] | null | undefined,
  date: string | null | undefined
): T[] {
  if (!plan || !date) return []
  return plan
    .filter((day) => day.date === date)
    .sort((a, b) => sessionSlot(a) - sessionSlot(b))
}

/** The session at `slot` on `date`, falling back to the day's first session. */
export function sessionAtSlot<T extends PlanSessionFields>(
  plan: T[] | null | undefined,
  date: string | null | undefined,
  slot: number | null | undefined
): T | undefined {
  const sessions = sessionsForDate(plan, date)
  if (sessions.length === 0) return undefined
  if (slot === null || slot === undefined) return sessions[0]
  return sessions.find((day) => sessionSlot(day) === slot) ?? sessions[0]
}

/**
 * Short label distinguishing one session of a two-a-day, e.g. "AM" or "2/2".
 *
 * Empty for a single-session day — a lone workout must not sprout a label that
 * implies there is another one.
 */
export function sessionLabel(
  day: PlanSessionFields,
  sessionCount: number
): string {
  if (sessionCount <= 1) return ''
  const hint = (day.timeOfDay ?? '').trim()
  if (hint) return hint.length <= 5 ? hint.toUpperCase() : hint
  return `${sessionSlot(day) + 1}/${sessionCount}`
}
