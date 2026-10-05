/**
 * Running pace: minutes and seconds per kilometre, in both directions (#716).
 *
 * The backend stores and computes with a number of seconds, because a time
 * string cannot be divided. A runner reads and types `4:10`. Both are needed,
 * and neither should be written twice — a pace shown as `4.17 /km` reads as
 * four-seventeen to nobody, and a pace parsed as a decimal silently turns
 * `4:10` into 4.
 */

/** Strictly `m:ss` or `mm:ss`. Anything else is not a pace. */
const PACE_PATTERN = /^(\d{1,2}):([0-5]\d)$/

/**
 * Seconds per kilometre for a `m:ss` string, or `null` when it is not a pace.
 *
 * Deliberately strict. A bare number is refused rather than read as seconds:
 * `4` is far more likely a half-typed `4:10` than a four-second kilometre, and
 * guessing which would set the reference every pace zone is cut from.
 */
export function parsePaceToSeconds(input: string): number | null {
  const match = PACE_PATTERN.exec(input.trim())
  if (!match) return null
  const seconds = parseInt(match[1], 10) * 60 + parseInt(match[2], 10)
  return seconds > 0 ? seconds : null
}

/** `250` → `'4:10'`. Empty string for anything that is not a pace. */
export function formatPaceFromSeconds(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return ''
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
