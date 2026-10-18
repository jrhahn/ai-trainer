/**
 * Running pace: minutes and seconds per kilometre, in both directions (#716).
 *
 * The backend stores and computes with a number of seconds, because a time
 * string cannot be divided. A runner reads and types `4:10`. Both are needed,
 * and neither should be written twice — a pace shown as `4.17 /km` reads as
 * four-seventeen to nobody, and a pace parsed as a decimal silently turns
 * `4:10` into 4.
 */

/**
 * `m:ss`, or `m.ss` / `m,ss` / `mss` as typed on a phone keypad, which has no
 * colon (ai-trainer-ops#131). The seconds are always two digits under 60.
 */
const PACE_PATTERN = /^(\d{1,2})[:.,]?([0-5]\d)$/

/**
 * Seconds per kilometre for a pace string, or `null` when it is not a pace.
 *
 * Strict where a guess would be wrong. A bare `4` is refused: it is far more
 * likely a half-typed `4:10` than four minutes flat. `4.5` is refused too,
 * because one seconds digit is a decimal (4.5 minutes is 4:30), and reading
 * it as 4:05 would set the reference every pace zone is cut from. `4.10`
 * reads as runners write it: four ten.
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
