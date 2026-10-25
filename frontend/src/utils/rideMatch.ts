import { english, type Translate } from '../i18n'
import type { RideMetricPoint, TrainingDay } from '../types/domain'
import { effectivePlannedMinutes, formatPlanDuration } from './planDuration'
import { sessionAtSlot, sessionSlot, sessionsForDate } from './planSessions'

/*
 * How a ride compares with its planned session: the score, its label, and the
 * question to the coach about it. Out of `DashboardPage` (ai-trainer-ops#274).
 */

/**
 * Days graded on how easy they stayed rather than on execution: rest and
 * recovery, plus any day too unspecified to compare against.
 *
 * A recovery day belongs here even when it prescribes a duration. Sending it
 * down the generic ladder instead scored a textbook easy spin that ran 26 min
 * long as 0/100 → "Needs work", because plans state their recovery watts in
 * prose and leave `targetPower` unset, leaving the clock as the only signal
 * (#551). The backend scorer is the authority; this stays in step with it.
 */
function isRestOrNoTargetPlan(plan: Partial<TrainingDay>): boolean {
  const workoutType = plan.workoutType?.toLowerCase()
  if (workoutType === 'rest' || workoutType === 'recovery') return true
  return !plan.durationMinutes && !plan.targetPower
}

/** Non-cycling plans (strength, yoga, swim, etc.) that have no power target. */
function isStrengthPlan(plan: Partial<TrainingDay>): boolean {
  const workoutType = plan.workoutType?.toLowerCase()
  return workoutType === 'strength' && !plan.targetPower
}

function isIntervalWorkoutPlan(plan: Partial<TrainingDay>): boolean {
  const workoutType = plan.workoutType?.toLowerCase()
  const title = plan.title?.toLowerCase() ?? ''
  return workoutType === 'intervals' || title.includes('interval') || title.includes('vo2')
}

function scoreDurationMatch(actualSeconds: number, plannedMinutes: number): number {
  const ratio = actualSeconds / 60 / plannedMinutes
  return Math.max(0, Math.round(100 - Math.abs(1 - ratio) * 200))
}

function scoreTargetPower(actualPower: number, targetPower: { low: number; high: number }): number {
  const { low, high } = targetPower
  if (actualPower >= low && actualPower <= high) {
    return 100
  }

  const edge = actualPower < low ? low : high
  const deviation = Math.abs(actualPower - edge) / edge
  return Math.max(0, Math.round(100 - deviation * 300))
}

function scoreStructuredIntervalPower(
  actualPower: number,
  plan: Partial<TrainingDay>
): number | null {
  // A heart-rate or pace interval has no power (ai-trainer-ops#117).
  const intervalPowerValues = plan.intervals
    ?.map((interval) => interval.power)
    .filter((power): power is number => typeof power === 'number' && power > 0) ?? []
  const inferredTarget = plan.targetPower ?? (
    intervalPowerValues.length > 0
      ? { low: Math.min(...intervalPowerValues), high: Math.max(...intervalPowerValues) }
      : null
  )
  if (!inferredTarget) return null

  const targetScore = scoreTargetPower(actualPower, inferredTarget)
  if (targetScore >= 90) return targetScore

  const targetLow = inferredTarget.low
  const targetHigh = inferredTarget.high
  if (!Number.isFinite(targetLow) || !Number.isFinite(targetHigh) || targetLow <= 0) {
    return targetScore
  }

  const plausibleSessionFloor = targetLow * 0.75
  if (actualPower >= plausibleSessionFloor && actualPower <= targetHigh) {
    const progress = Math.min(1, (actualPower - plausibleSessionFloor) / (targetLow - plausibleSessionFloor))
    return Math.max(targetScore, Math.round(85 + progress * 10))
  }

  return targetScore
}

// The whole session's average sits below the interval target because of the
// warm-up and recoveries; within this share of it the session still counts as
// done. The same heuristics as backend/services/plan_compliance.py (#133).
const HR_SESSION_FLOOR = 0.9
const PACE_SESSION_FLOOR = 0.8

/** "4:30/km" or "4:30" as seconds per kilometre, else null. */
export function parsePaceSecondsPerKm(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const match = /^\s*(\d{1,2}):([0-5]\d)\s*(?:\/\s*km)?\s*$/i.exec(value)
  if (!match) return null
  const seconds = Number(match[1]) * 60 + Number(match[2])
  return seconds > 0 ? seconds : null
}

/** What the planned intervals are prescribed in; power first, as on the server. */
export function intervalTargetKind(plan: Partial<TrainingDay>): 'power' | 'heart rate' | 'pace' | null {
  const intervals = plan.intervals ?? []
  if (plan.targetPower || intervals.some((iv) => typeof iv.power === 'number' && iv.power > 0)) return 'power'
  if (intervals.some((iv) => typeof iv.heartRate === 'number' && iv.heartRate >= 30 && iv.heartRate <= 250)) {
    return 'heart rate'
  }
  if (intervals.some((iv) => parsePaceSecondsPerKm(iv.pace) != null)) return 'pace'
  return null
}

function scoreAgainstFloor(actual: number, target: { low: number; high: number }, floor: number): number {
  const targetScore = scoreTargetPower(actual, target)
  if (targetScore >= 90) return targetScore
  const sessionFloor = target.low * floor
  if (actual >= sessionFloor && actual <= target.high && target.low > sessionFloor) {
    const progress = Math.min(1, (actual - sessionFloor) / (target.low - sessionFloor))
    return Math.max(targetScore, Math.round(85 + progress * 10))
  }
  return targetScore
}

/** Heart-rate or pace intervals against the session's average; null when it lacks the signal (#133). */
export function scoreStructuredIntervalEffort(ride: RideMetricPoint, plan: Partial<TrainingDay>): number | null {
  const kind = intervalTargetKind(plan)
  const intervals = plan.intervals ?? []
  if (kind === 'heart rate') {
    if (!ride.avgHeartRateBpm) return null
    const rates = intervals
      .map((iv) => iv.heartRate)
      .filter((hr): hr is number => typeof hr === 'number' && hr >= 30 && hr <= 250)
    return scoreAgainstFloor(ride.avgHeartRateBpm, { low: Math.min(...rates), high: Math.max(...rates) }, HR_SESSION_FLOOR)
  }
  if (kind === 'pace') {
    if (!ride.distanceKm || !ride.durationSeconds || ride.distanceKm <= 0 || ride.durationSeconds <= 0) return null
    const speed = (ride.distanceKm * 1000) / ride.durationSeconds
    // A pace is time per distance, so the slower pace is the lower speed.
    const speeds = intervals
      .map((iv) => parsePaceSecondsPerKm(iv.pace))
      .filter((pace): pace is number => pace != null)
      .map((pace) => 1000 / pace)
    return scoreAgainstFloor(speed, { low: Math.min(...speeds), high: Math.max(...speeds) }, PACE_SESSION_FLOOR)
  }
  return null
}

/** Returns 0-100 match score, or null when not enough data to compare. */
export function computeMatchScore(
  ride: RideMetricPoint,
  plan: Partial<TrainingDay>
): number | null {
  const actualPower = ride.normalizedPowerW ?? ride.avgPowerW

  if (isRestOrNoTargetPlan(plan)) {
    if (ride.tss != null) {
      if (ride.tss <= 30) return 80
      if (ride.tss <= 65) return 55
      return 20
    }
    // No TSS available: use duration ratio if both sides are known, otherwise default to no-data OK
    if (ride.durationSeconds && plan.durationMinutes) {
      // A prescribed duration window makes any in-range actual on-target (#368).
      return scoreDurationMatch(ride.durationSeconds, effectivePlannedMinutes(plan, ride.durationSeconds))
    }
    return 90
  }

  // Strength/non-power plans: score purely on duration completion (0–100 %).
  if (isStrengthPlan(plan)) {
    if (!ride.durationSeconds || !plan.durationMinutes) return 90
    const planned = effectivePlannedMinutes(plan, ride.durationSeconds)
    return Math.min(100, Math.round((ride.durationSeconds / 60 / planned) * 100))
  }

  const parts: number[] = []
  const intervalWorkout = isIntervalWorkoutPlan(plan)

  if (ride.durationSeconds != null && plan.durationMinutes) {
    const planned = effectivePlannedMinutes(plan, ride.durationSeconds)
    let durationScore = scoreDurationMatch(ride.durationSeconds, planned)
    if (intervalWorkout) {
      const ratio = ride.durationSeconds / 60 / planned
      if (ratio > 1 && ratio <= 2) {
        durationScore = Math.max(durationScore, 40)
      }
    }
    parts.push(durationScore)
  }

  const effortKind = intervalWorkout ? intervalTargetKind(plan) : null
  if (effortKind === 'heart rate' || effortKind === 'pace') {
    // Scored on the signal the plan set, never on watts it did not ask for (#133).
    const effortScore = scoreStructuredIntervalEffort(ride, plan)
    if (effortScore != null) parts.push(effortScore)
  } else if (actualPower != null) {
    if (intervalWorkout) {
      const intervalPowerScore = scoreStructuredIntervalPower(actualPower, plan)
      parts.push(intervalPowerScore ?? (ride.tss != null && ride.tss >= 70 ? 88 : 70))
    } else if (plan.targetPower) {
      parts.push(scoreTargetPower(actualPower, plan.targetPower))
    }
  }

  if (intervalWorkout && parts.length === 2) {
    return Math.round(parts[0] * 0.25 + parts[1] * 0.75)
  }

  return parts.length > 0 ? Math.round(parts.reduce((a, b) => a + b) / parts.length) : null
}

export function matchScoreLabel(score: number | null, plan: Partial<TrainingDay>, labelOverride?: string | null): string {
  if (labelOverride) return labelOverride

  if (score === null) return '?'

  if (isRestOrNoTargetPlan(plan)) {
    if (score >= 90) return 'OK'
    if (score >= 75) return 'Recovery'
    if (score >= 40) return 'Warning'
    return 'Too much'
  }

  if (isStrengthPlan(plan)) {
    if (score >= 85) return 'Done'
    if (score >= 55) return 'Partial'
    if (score >= 25) return 'Short'
    return 'Skipped'
  }

  return score >= 90
    ? 'Perfect'
    : score >= 75
      ? 'Solid'
      : score >= 60
        ? 'Close'
        : score >= 40
          ? 'Off plan'
          : 'Needs work'
}

export function planForRide(
  ride: RideMetricPoint,
  trainingPlan: TrainingDay[]
): Partial<TrainingDay> | null {
  const snapshot = ride.matchedPlanSnapshot ?? null
  const sessions = sessionsForDate(trainingPlan, ride.activityDate)

  // A matched ride records the session it chose, slot included — so ask for that
  // session rather than for "the day", which on a two-a-day is the morning one
  // whatever the athlete actually rode (#648).
  if (ride.matchedPlanDate === ride.activityDate) {
    const matched = sessionAtSlot(trainingPlan, ride.activityDate, sessionSlot(snapshot))
    if (matched) return matched
  }

  // Unmatched, one session: nothing to confuse it with.
  if (sessions.length === 1) return sessions[0]

  const snapshotDate = typeof snapshot?.date === 'string' ? snapshot.date : null
  const staleMatchedDate = !!ride.matchedPlanDate && ride.matchedPlanDate !== ride.activityDate
  const staleSnapshotDate = !!snapshotDate && snapshotDate !== ride.activityDate

  // Still worth trying before giving up: a snapshot names one specific session,
  // and it survives the plan day dropping out of the loaded window.
  if (snapshot && !staleMatchedDate && !staleSnapshotDate) {
    return snapshot
  }

  // Two sessions and nothing that says which: we cannot tell, and "planned:
  // Morning intervals" against what may have been the evening ride is worse
  // than saying nothing at all.
  return null
}

export interface RideBadge {
  score: number | null
  label: string
}

/**
 * The compliance label to render for a ride.
 *
 * The backend's persisted score/label wins whenever it has one: it is scored
 * against the plan day the ride was actually matched to and is the same value
 * handed to the coach, so the card and the chat cannot disagree (#551). The
 * local computation remains as the fallback for rides the backend has not
 * scored — unmatched activities, and anything imported before the backfill.
 *
 * A word, not a coloured badge, since the owner's review of 2026-10-24: green,
 * lime, amber, orange, red and blue side by side was the "Kunterbunt" they
 * asked to remove. "Perfect" and "Needs work" say it in words.
 */
export function rideMatchBadge(
  ride: RideMetricPoint,
  plan: Partial<TrainingDay> | null
): RideBadge {
  const backendLabel = ride.labelOverride ?? ride.matchLabel ?? null
  const score = ride.matchScore ?? (plan ? computeMatchScore(ride, plan) : null)

  if (!plan) return { score, label: backendLabel ?? '?' }
  return { score, label: matchScoreLabel(score, plan, backendLabel) }
}


/** Builds a natural, trainer-style prompt asking for feedback on a ride vs plan.
 *
 * Sent in the athlete's name, so in the interface's language: the backend reads
 * the reply language off it, and an English prompt made the coach answer, and
 * the summaries after it, in English (ai-trainer-ops#121).
 */
export function buildMatchCoachPrompt(
  ride: RideMetricPoint,
  plan: Partial<TrainingDay>,
  score: number | null,
  t: Translate = english
): string {
  const rideName = ride.activityName ?? t('my ride')
  const actualMin = ride.durationSeconds ? Math.round(ride.durationSeconds / 60) : null
  const actualPower = ride.normalizedPowerW ?? ride.avgPowerW

  const actualParts = [
    actualMin ? `${actualMin} min` : null,
    actualPower ? `${Math.round(actualPower)}W` : null,
  ].filter(Boolean).join(', ')

  const planParts = [
    // English on purpose: "1h 30m" inside the prompt is read by the model, not
    // shown as an interface unit (ai-trainer-ops#121).
    plan.durationMinutes ? formatPlanDuration(plan) : null,
    plan.targetPower ? `${plan.targetPower.low}–${plan.targetPower.high}W` : null,
  ].filter(Boolean).join(', ')

  const planLabel = plan.title ?? plan.workoutType ?? t('the planned session')
  const label = t(matchScoreLabel(score, plan, ride.labelOverride ?? ride.matchLabel))
  const labelStr =
    score !== null
      ? t('The displayed match label is "{label}" with a {score}% score.', { label, score })
      : t('The displayed match label is "{label}".', { label })
  const caution = t('Treat this as the current app state and do not invent data-quality causes unless the activity data explicitly shows that.')

  return t(
    'Just finished "{ride}" — {actual}. The plan had "{plan}" down for {planned}. {label} {caution} What\'s your take — did I execute it well, and anything I should tweak next time?',
    { ride: rideName, actual: actualParts, plan: planLabel, planned: planParts, label: labelStr, caution }
  )
}
