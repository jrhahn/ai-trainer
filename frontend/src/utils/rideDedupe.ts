import type { RideMetricPoint } from '../types/domain'
import { LOGGED_SOURCE } from './enteredActivity'

/**
 * One row per activity on the dashboard, even when two sources delivered it
 * or one recording sits inside a longer one. Out of `DashboardPage` (ai-trainer-ops#274).
 */

const CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO = 0.8
const CONTAINED_DUPLICATE_TIME_TOLERANCE_MS = 10 * 60 * 1000

export function rideActivityKey(ride: RideMetricPoint): string {
  return ride.externalActivityId
    ? `external:${ride.externalActivityId}`
    : `strava:${ride.stravaActivityId}`
}

export function rideActivityRefreshId(ride: RideMetricPoint): string | number {
  return ride.externalActivityId || ride.stravaActivityId
}

function normalizeActivityText(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replace(/\s+/g, ' ')
}

function activityFamily(sportType: string | null | undefined): string {
  const normalized = normalizeActivityText(sportType).replace(/[^a-z0-9]/g, '')
  if (
    normalized.includes('ride') ||
    normalized.includes('cycling') ||
    normalized.includes('bike')
  ) {
    return 'cycling'
  }
  if (normalized.includes('weight') || normalized.includes('strength')) return 'strength'
  if (normalized.includes('run')) return 'running'
  return normalized || 'activity'
}

function roundedDurationMinutes(seconds: number | undefined): number | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null
  return Math.round(seconds / 60)
}

function activityIdentityTokens(value: string | null | undefined): Set<string> {
  const generic = new Set([
    'activity',
    'bicycling',
    'bike',
    'biking',
    'cycling',
    'fahrt',
    'mountain',
    'mtb',
    'ride',
    'road',
  ])
  const normalized = normalizeActivityText(value).replace(/[^a-z0-9\s]/g, ' ')
  return new Set(
    normalized
      .split(/\s+/)
      .filter((token) => token && !generic.has(token) && !/^\d+$/.test(token)),
  )
}

function activityNamesCompatible(
  value: string | null | undefined,
  other: string | null | undefined
): boolean {
  const normalized = normalizeActivityText(value)
  const otherNormalized = normalizeActivityText(other)
  if (!normalized || !otherNormalized) return false
  if (normalized === otherNormalized) return true

  const tokens = activityIdentityTokens(normalized)
  const otherTokens = activityIdentityTokens(otherNormalized)
  if (tokens.size === 0 || tokens.size !== otherTokens.size) return false
  return [...tokens].every((token) => otherTokens.has(token))
}

function rideVisibleFingerprint(ride: RideMetricPoint): string {
  return [
    normalizeActivityText(ride.sportType),
    normalizeActivityText(ride.activityName),
    ride.activityDate,
    ride.activityStartDatetime ?? '',
    ride.durationSeconds ?? '',
  ].join('|')
}

function parseActivityTimestamp(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

function hasExplicitTimezone(value: string | null | undefined): boolean {
  return /(?:z|[+-]\d\d:?\d\d)$/i.test(value ?? '')
}

function activityStartDistanceMs(
  value: string | null | undefined,
  other: string | null | undefined,
): number | null {
  if (!value || !other) return null
  const bothHaveSameTimezoneShape =
    hasExplicitTimezone(value) === hasExplicitTimezone(other)
  const left = bothHaveSameTimezoneShape ? value : value.slice(0, 19)
  const right = bothHaveSameTimezoneShape ? other : other.slice(0, 19)
  const start = parseActivityTimestamp(left)
  const otherStart = parseActivityTimestamp(right)
  if (start == null || otherStart == null) return null
  return Math.abs(start - otherStart)
}

function normalizedActivityStartMs(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  if (!Number.isNaN(parsed)) return parsed
  const localShape = Date.parse(value.slice(0, 19))
  return Number.isNaN(localShape) ? null : localShape
}

function rideTimeIntervalMs(ride: RideMetricPoint): [number, number] | null {
  const start = normalizedActivityStartMs(ride.activityStartDatetime)
  if (start == null || !ride.durationSeconds || ride.durationSeconds <= 0) return null
  return [start, start + ride.durationSeconds * 1000]
}

function areContainedDuplicateRides(
  ride: RideMetricPoint,
  other: RideMetricPoint
): boolean {
  const interval = rideTimeIntervalMs(ride)
  const otherInterval = rideTimeIntervalMs(other)
  if (!interval || !otherInterval) return false

  const [start, end] = interval
  const [otherStart, otherEnd] = otherInterval
  const duration = end - start
  const otherDuration = otherEnd - otherStart
  if (duration <= 0 || otherDuration <= 0) return false
  if (Math.abs(duration - otherDuration) <= 15 * 60 * 1000) return false

  const shorter = Math.min(duration, otherDuration)
  const overlap = Math.max(0, Math.min(end, otherEnd) - Math.max(start, otherStart))
  if (overlap / shorter < CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO) return false

  if (duration <= otherDuration) {
    return (
      start >= otherStart - CONTAINED_DUPLICATE_TIME_TOLERANCE_MS &&
      end <= otherEnd + CONTAINED_DUPLICATE_TIME_TOLERANCE_MS
    )
  }
  return (
    otherStart >= start - CONTAINED_DUPLICATE_TIME_TOLERANCE_MS &&
    otherEnd <= end + CONTAINED_DUPLICATE_TIME_TOLERANCE_MS
  )
}

function areNearDuplicateRides(ride: RideMetricPoint, other: RideMetricPoint): boolean {
  if (ride.activityDate !== other.activityDate) return false
  if (activityFamily(ride.sportType) !== activityFamily(other.sportType)) return false

  if (!activityNamesCompatible(ride.activityName, other.activityName)) return false

  const durationMin = roundedDurationMinutes(ride.durationSeconds)
  const otherDurationMin = roundedDurationMinutes(other.durationSeconds)
  if (durationMin == null || otherDurationMin == null) return false
  if (Math.abs(durationMin - otherDurationMin) > 15) {
    return areContainedDuplicateRides(ride, other)
  }

  const startDistanceMs = activityStartDistanceMs(
    ride.activityStartDatetime,
    other.activityStartDatetime,
  )
  if (startDistanceMs == null) return true

  return startDistanceMs <= 30 * 60 * 1000
}

function preferredVisibleRide(
  ride: RideMetricPoint,
  other: RideMetricPoint
): RideMetricPoint {
  const duration = ride.durationSeconds ?? 0
  const otherDuration = other.durationSeconds ?? 0
  if (Math.abs(duration - otherDuration) > 15 * 60) {
    return duration > otherDuration ? ride : other
  }
  return ride
}

export function dedupeRideMetricsByActivity(rides: RideMetricPoint[]): RideMetricPoint[] {
  const seen = new Set<string>()
  const unique: RideMetricPoint[] = []
  for (const ride of rides) {
    const identityKey = rideActivityKey(ride)
    // A hand-entered session carries no start time and a shared name, so the
    // fuzzy checks below would take two runs on one day for one. Its id is
    // its whole identity (ai-trainer-ops#47).
    if (ride.activitySource === LOGGED_SOURCE) {
      if (!seen.has(identityKey)) unique.push(ride)
      seen.add(identityKey)
      continue
    }
    const visibleKey = rideVisibleFingerprint(ride)
    const duplicateIndex = unique.findIndex((existing) =>
      areNearDuplicateRides(ride, existing)
    )
    if (seen.has(identityKey) || seen.has(visibleKey)) continue
    if (duplicateIndex >= 0) {
      unique[duplicateIndex] = preferredVisibleRide(ride, unique[duplicateIndex])
      seen.add(identityKey)
      seen.add(visibleKey)
      continue
    }
    seen.add(identityKey)
    seen.add(visibleKey)
    unique.push(ride)
  }
  return unique
}
