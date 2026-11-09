import type { RideMetricPoint } from '../store/useAppStore'
import { activityNoun } from './activityType'

/** The rungs a device or a provider measured: the backend's
 *  domain/training_load.MEASURED_LOAD_SOURCES. A row with no source predates
 *  #579, and the coach's prompts count it as measured too (format_load). */
const MEASURED_LOAD_SOURCES = new Set(['provider', 'power', 'pace'])

/** What an estimated load came from, as the athlete reads it (ai-trainer-ops#311). */
const ESTIMATED_FROM: Record<string, string> = {
  heart_rate: 'estimated from your heart rate',
  rpe: 'estimated from the effort you logged',
  duration: 'estimated from the duration only',
}

export interface RideLoad {
  /** The load, rounded as the coach's prompts round it. */
  value: number
  /** How it was estimated, or null when it was measured. */
  estimatedFrom: string | null
}

/** A ride's load as expert mode shows it, or null when it has none. */
export function rideLoad(ride: RideMetricPoint): RideLoad | null {
  if (ride.tss == null || !Number.isFinite(ride.tss)) return null
  const source = ride.tssSource ?? null
  const estimatedFrom =
    source == null || MEASURED_LOAD_SOURCES.has(source) ? null : (ESTIMATED_FROM[source] ?? 'estimated')
  return { value: Math.round(ride.tss), estimatedFrom }
}

/** How long back a ride is compared, and how many rides of its sport that
 *  window must hold before a word means anything (ai-trainer-ops#313). */
export const LOAD_WINDOW_DAYS = 42
export const LOAD_MIN_RIDES = 6

export type LoadLevel = 'easy' | 'moderate' | 'hard'

function daysBetween(earlier: string, later: string): number {
  return Math.round((Date.parse(`${later}T00:00:00Z`) - Date.parse(`${earlier}T00:00:00Z`)) / 86_400_000)
}

/**
 * A ride's load in a word, measured against the athlete's own rides of the same
 * sport in the six weeks up to that day (ai-trainer-ops#313, the owner's "Weg 2").
 * The ride itself counts among them. The lower third is easy, the upper third hard. Null when the
 * ride has no load or the window holds fewer than LOAD_MIN_RIDES rides: a word
 * from three rides would say more than it knows.
 */
export function rideLoadLevel(ride: RideMetricPoint, history: RideMetricPoint[]): LoadLevel | null {
  const load = rideLoad(ride)
  if (!load) return null
  const sport = activityNoun(ride.sportType)
  const seen = new Set<string>()
  const loads: number[] = []
  for (const other of history) {
    const key = other.externalActivityId ? `external:${other.externalActivityId}` : `strava:${other.stravaActivityId}`
    if (seen.has(key)) continue
    if (activityNoun(other.sportType) !== sport) continue
    if (other.tss == null || !Number.isFinite(other.tss)) continue
    const age = daysBetween(other.activityDate, ride.activityDate)
    if (Number.isNaN(age) || age < 0 || age >= LOAD_WINDOW_DAYS) continue
    seen.add(key)
    loads.push(other.tss)
  }
  if (loads.length < LOAD_MIN_RIDES) return null
  // Its rank among them, 0 the lightest and 1 the hardest; equal loads share
  // the middle of their places, so a run of identical rides reads moderate.
  const value = ride.tss!
  const below = loads.filter((other) => other < value).length
  const equal = loads.filter((other) => other === value).length
  const share = (below + Math.max(equal - 1, 0) / 2) / (loads.length - 1)
  if (share < 1 / 3) return 'easy'
  if (share >= 2 / 3) return 'hard'
  return 'moderate'
}
