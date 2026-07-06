/**
 * Planned-duration helpers for training days (#368).
 *
 * A planned activity may prescribe a duration *window* (e.g. 2.5–3h endurance)
 * via durationMinMinutes / durationMaxMinutes instead of a single durationMinutes.
 * A single value is simply the degenerate window where min === max. These helpers
 * give a consistent read/display/scoring view over either shape.
 */

export interface PlanDurationFields {
  durationMinutes?: number | null
  durationMinMinutes?: number | null
  durationMaxMinutes?: number | null
}

function positive(value: number | null | undefined): number | undefined {
  return typeof value === 'number' && value > 0 ? value : undefined
}

/**
 * The planned duration window `[lo, hi]` in minutes, or null when the day has
 * no usable duration (e.g. a rest day). A missing bound falls back to the
 * scalar `durationMinutes`, so a single-value day reads back as `[v, v]`.
 */
export function planDurationRange(day: PlanDurationFields | null | undefined): [number, number] | null {
  if (!day) return null
  const min = positive(day.durationMinMinutes)
  const max = positive(day.durationMaxMinutes)
  const scalar = positive(day.durationMinutes)
  const lo = min ?? scalar ?? max
  const hi = max ?? scalar ?? min
  if (lo === undefined || hi === undefined) return null
  return lo <= hi ? [lo, hi] : [hi, lo]
}

function formatMinutes(min: number): string {
  if (min % 60 === 0) return `${min / 60}h`
  if (min < 60) return `${min} min`
  const hours = Math.floor(min / 60)
  const rest = min % 60
  return `${hours}h ${rest}m`
}

/** Bound label in hours, trimming trailing zeros: 150 → "2.5", 180 → "3". */
function hoursLabel(min: number): string {
  return String(Math.round((min / 60) * 100) / 100)
}

/**
 * Human-readable planned duration: "3h" / "45 min" for a single value, or a
 * window like "2.5–3h" (both bounds ≥ 1h) or "50–70 min". Empty when unknown.
 */
export function formatPlanDuration(day: PlanDurationFields | null | undefined): string {
  const range = planDurationRange(day)
  if (!range) return ''
  const [lo, hi] = range
  if (lo === hi) return formatMinutes(lo)
  if (lo >= 60 && hi >= 60) return `${hoursLabel(lo)}–${hoursLabel(hi)}h`
  if (lo < 60 && hi < 60) return `${lo}–${hi} min`
  return `${formatMinutes(lo)}–${formatMinutes(hi)}`
}

/**
 * The planned minutes to compare an actual session against: an actual inside
 * the window is on-target (returns the actual, so a ratio-based score is 1.0);
 * otherwise the nearest bound. Falls back to `durationMinutes` when no window.
 */
export function effectivePlannedMinutes(
  day: PlanDurationFields | null | undefined,
  actualSeconds: number,
): number {
  const range = planDurationRange(day)
  if (!range) return positive(day?.durationMinutes) ?? 0
  const [lo, hi] = range
  const actualMinutes = actualSeconds / 60
  if (actualMinutes < lo) return lo
  if (actualMinutes > hi) return hi
  return actualMinutes
}
