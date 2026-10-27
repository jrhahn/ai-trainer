import type { RideMetricPoint } from '../store/useAppStore'

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
