/**
 * Which sport a planned session prescribes (#710).
 *
 * `workoutType` says what a session is *for* ("endurance", "intervals"); `sport`
 * says what the athlete actually does. The field is absent on every day written
 * before it existed *and* on every cycling day — the backend omits the default
 * so storage stays byte-stable — so it must never be read directly.
 *
 * The vocabulary is the backend's (`services/activity_identity.training_sport`):
 * one set of names shared by planned sessions and imported activities, which is
 * what lets `utils/activityType` label both.
 */

import { activityNoun } from './activityType'

export const PLAN_SPORT_CYCLING = 'cycling'
export const PLAN_SPORT_RUNNING = 'running'
export const PLAN_SPORT_STRENGTH = 'strength'

/** The sports the plan editor offers, in the order it offers them. */
export const PLANNABLE_SPORTS = [
  PLAN_SPORT_CYCLING,
  PLAN_SPORT_RUNNING,
  PLAN_SPORT_STRENGTH,
] as const

/** A planned session's sport, defaulting to cycling for legacy and missing values.
 *
 * Takes only the fields it reads, so a `Partial<TrainingDay>` snapshot can be
 * asked for its sport without a cast.
 */
export function planSport(
  day: { sport?: string | null; workoutType?: string | null } | null | undefined
): string {
  const sport = (day?.sport ?? '').trim().toLowerCase()
  if (sport) return sport
  // The one sport the pre-#710 vocabulary could state. Every other workout type
  // describes a purpose and says nothing about which sport the session is.
  if ((day?.workoutType ?? '').trim().toLowerCase() === PLAN_SPORT_STRENGTH) {
    return PLAN_SPORT_STRENGTH
  }
  return PLAN_SPORT_CYCLING
}

/** Whether this session is a bike session — i.e. whether watts mean anything here. */
export function isCyclingSession(
  day: { sport?: string | null; workoutType?: string | null } | null | undefined
): boolean {
  return planSport(day) === PLAN_SPORT_CYCLING
}

/** What to call the session in prose: "ride", "run", "strength session". */
export function planSportNoun(
  day: { sport?: string | null; workoutType?: string | null } | null | undefined
): string {
  return activityNoun(planSport(day))
}

/** Display label for a sport, for pickers and badges. */
export function planSportLabel(sport: string): string {
  const normalized = (sport ?? '').trim().toLowerCase()
  if (!normalized) return ''
  switch (normalized) {
    case PLAN_SPORT_CYCLING:
      return 'Cycling'
    case PLAN_SPORT_RUNNING:
      return 'Running'
    case PLAN_SPORT_STRENGTH:
      return 'Strength'
    default:
      return normalized.charAt(0).toUpperCase() + normalized.slice(1)
  }
}
