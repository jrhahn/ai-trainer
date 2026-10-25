import { useEffect, useRef, useState } from 'react'
import { english, formatLongDate, localeTag, useLanguage, useT, type Language, type Translate } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { sportTypeText } from '../utils/activityType'
import { BatteryFull, BatteryLow, BatteryMedium, Flag, Pencil, Plus, Trash2 } from 'lucide-react'
import { button, card, cardTitle, dotted, iconButton, iconDeleteButton, note } from '../components/ui'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import PlanChangesPanel from '../components/PlanChangesPanel'
import TrainingCalendar from '../components/TrainingCalendar'
import AthletePerformanceModelCard from '../components/AthletePerformanceModelCard'
import AmbiguousMatchResolver from '../components/AmbiguousMatchResolver'
import SessionHero from '../components/SessionHero'
import DayBoardWhy from '../components/DayBoardWhy'
import { sessionAtSlot, sessionSlot, sessionsForDate } from '../utils/planSessions'
import { loggedSessionKeys, nextUnfinishedSession } from '../utils/sessionCompletion'
import { LOGGED_SOURCE, enteredActivitySlot } from '../utils/enteredActivity'
import SeasonCountdown from '../components/SeasonCountdown'
import ColdStartNote from '../components/ColdStartNote'
import PlanRebuildNotice from '../components/PlanRebuildNotice'
import WeekStrip from '../components/WeekStrip'
import SeasonPlan from '../components/SeasonPlan'
import Modal from '../components/Modal'
import LogSessionDialog, { type EditedActivity } from '../components/LogSessionDialog'
import { useEnteredActivityActions } from '../hooks/useEnteredActivityActions'
import AddRaceDialog from '../components/AddRaceDialog'
import { useLogPlannedSession } from '../hooks/useLogPlannedSession'
import SessionPurposeQuestion from '../components/SessionPurposeQuestion'
import { WeatherIcon } from '../components/WeatherBadge'
import { formatTemperature } from '../utils/weather'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import PredictionScoreboard from '../components/PredictionScoreboard'
import { useScoredPredictions } from '../hooks/useScoredPredictions'
import { processPendingFeedbacks, refreshLoginSummary, refreshTrainingStatus } from '../services/ai'
import { setRideLegs } from '../services/user'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { effectivePlannedMinutes, formatPlanDuration } from '../utils/planDuration'
import { accountKey, readLocal, writeLocal } from '../lib/storage'

// Both per account (ai-trainer-ops#269): two friends on one browser shared them,
// and the second saw "new" badges from the first one's last login.
export const PREV_LOGIN_KEY = 'ai_trainer_previous_login'
export const SUMMARY_REFRESH_KEY = 'ai_trainer_summary_refresh_activity_ids'
const SUMMARY_REFRESH_VERSION = 'latest-activity-v6'
const CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO = 0.8
const CONTAINED_DUPLICATE_TIME_TOLERANCE_MS = 10 * 60 * 1000

/** Quick leg-freshness ratings tappable directly on each activity card.
 *
 * A battery rather than a traffic light (ai-trainer-ops#51.7). The three
 * circles were 🟢🟡🔴, which carried the whole meaning in colour — nothing at
 * all to a red-green colourblind athlete, and three empty boxes in a browser
 * with no emoji font. A battery says the same thing by how full it is. The
 * three are grey, and the chosen one takes the accent (owner's review,
 * 2026-10-24: one accent, no green, amber and red side by side).
 */
const LEG_FEELINGS = [
  { value: 'fresh', Icon: BatteryFull, label: 'Fresh' },
  { value: 'normal', Icon: BatteryMedium, label: 'Normal' },
  { value: 'heavy', Icon: BatteryLow, label: 'Heavy' },
] as const

export function formatDuration(seconds: number | undefined, language: Language = 'en'): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  // "1 h 30 min" in German, "1h 30m" in English (ai-trainer-ops#121).
  if (h > 0) return language === 'de' ? `${h} h ${m} min` : `${h}h ${m}m`
  return `${m} min`
}

export function rideActivityKey(ride: RideMetricPoint): string {
  return ride.externalActivityId
    ? `external:${ride.externalActivityId}`
    : `strava:${ride.stravaActivityId}`
}

function rideActivityRefreshId(ride: RideMetricPoint): string | number {
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

function dedupeRideMetricsByActivity(rides: RideMetricPoint[]): RideMetricPoint[] {
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

export function splitTrainingSummary(raw: string): {
  intro: string
  bullets: { label?: string; text: string }[]
} {
  let intro = ''
  let rawBullets: string[] = []

  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      const summary = parsed as Record<string, unknown>
      const parsedBullets = Array.isArray(summary.bulletPoints)
        ? summary.bulletPoints
        : Array.isArray(summary.bullets)
          ? summary.bullets
          : null

      if ('intro' in summary && parsedBullets) {
        intro = String(summary.intro)
        rawBullets = parsedBullets.map(String)
      } else {
        rawBullets = Object.values(summary).map(String)
      }
    }
  } catch {
    const lines = raw.split('\n')
    const introLines: string[] = []
    for (const line of lines) {
      if (line.startsWith('- ')) {
        rawBullets.push(line)
      } else {
        introLines.push(line)
      }
    }
    intro = introLines.join(' ').trim()
  }

  const bullets = rawBullets.map((b) => {
    const normalized = b.replace(/\n+\s*/g, ' ').trim()
    const withoutDash = normalized.startsWith('- ') ? normalized.slice(2) : normalized
    const decoded = withoutDash
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
    const colonIdx = decoded.indexOf(': ')
    if (colonIdx > 0) {
      return { label: decoded.slice(0, colonIdx), text: decoded.slice(colonIdx + 2) }
    }
    return { text: decoded }
  })

  return { intro, bullets }
}

export default function DashboardPage() {
  const t = useT()
  const planText = usePlanText()
  const language = useLanguage()
  const rideKeysAwaitingAnswer = useRef<Set<string>>(new Set())
  const { hasScored: hasScoredPredictions } = useScoredPredictions()
  const { userProfile, trainingPlan, authToken, stravaConnection, isExpertMode, riderAssessment, setRiderAssessment, rideMetricsHistory, updateRideMetricLegs, setPendingCoachMessage, showWarning } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      isExpertMode: s.isExpertMode,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      rideMetricsHistory: s.rideMetricsHistory,
      updateRideMetricLegs: s.updateRideMetricLegs,
      setPendingCoachMessage: s.setPendingCoachMessage,
      showWarning: s.showWarning,
    }))
  )

  const summaryTriggeredRef = useRef(false)
  const statusTriggeredRef = useRef(false)
  const summaryRefreshKeyRef = useRef<string | null>(null)
  const [calendarOpen, setCalendarOpen] = useState(false)
  const [addActivityOpen, setAddActivityOpen] = useState(false)
  const [addRaceOpen, setAddRaceOpen] = useState(false)
  const [editingActivity, setEditingActivity] = useState<EditedActivity | null>(null)
  // The session being logged from the card, if any (ai-trainer-ops#52).
  const [loggingSession, setLoggingSession] = useState<TrainingDay | null>(null)
  const { logSession } = useLogPlannedSession()
  // Which day the hero is showing.  Owned here rather than in the strip because
  // the hero is the thing that renders it, and the two are siblings (#634).
  // Lazy, not `today`: that is declared further down this component.
  const [selectedDate, setSelectedDate] = useState(() => formatLocalDate(new Date()))
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [prevLoginDate, setPrevLoginDate] = useState<string | null>(null)

  // Quick "how the legs felt" tap on an activity card. Optimistically update the
  // store, persist to the backend, and revert on failure. Tapping the active
  // rating again clears it (back to unset, which is ignored everywhere).
  const handleSetLegs = (ride: RideMetricPoint, legs: 'fresh' | 'normal' | 'heavy') => {
    if (!authToken) return
    const next = ride.feelLegs === legs ? null : legs
    const previous = ride.feelLegs ?? null
    updateRideMetricLegs(ride.stravaActivityId, next)
    void setRideLegs(authToken, ride.stravaActivityId, next, ride.externalActivityId).catch(() => {
      updateRideMetricLegs(ride.stravaActivityId, previous)
    })
  }

  const account = userProfile?.email ?? ''
  useEffect(() => {
    if (!account) return
    const key = accountKey(PREV_LOGIN_KEY, account)
    const stored = readLocal(key)
    setPrevLoginDate(stored ? formatLocalDate(new Date(stored)) : null)
    writeLocal(key, new Date().toISOString())
  }, [account])
  const importProgress = useImportProgress()

  // keep sync running so analysis status updates remain active
  const { analysisStatus, analysisError } = useStravaSync()

  const firstName = userProfile?.name?.trim().split(/\s+/)[0] ?? ''

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const today = formatLocalDate(new Date())

  const analyzedActivities = Math.min(importProgress.processed, importProgress.total)
  const consumedTokens = userProfile?.consumedTokens ?? 0
  const loginSummary = riderAssessment?.loginSummary
    ? splitTrainingSummary(riderAssessment.loginSummary)
    : null
  const hasActivityProgress = !!stravaConnection && importProgress.status !== 'idle' && importProgress.total > 0
  const progressPct = hasActivityProgress
    ? Math.round((analyzedActivities / importProgress.total) * 100)
    : 0

  const threeDaysAgo = formatLocalDate(new Date(Date.now() - 3 * 24 * 60 * 60 * 1000))
  const sevenDaysAgo = formatLocalDate(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000))

  // Extend the window to 7 days if there are new (since last login) activities
  // that fall between 3 and 7 days ago
  const hasNewBeyond3Days =
    prevLoginDate !== null &&
    rideMetricsHistory.some(
      (r) =>
        r.activityDate >= sevenDaysAgo &&
        r.activityDate < threeDaysAgo &&
        r.activityDate >= prevLoginDate!
    )
  const recentWindow = hasNewBeyond3Days ? sevenDaysAgo : threeDaysAgo

  const recentRides = dedupeRideMetricsByActivity(rideMetricsHistory
    .filter((r) => r.activityDate >= recentWindow && r.activityDate <= today)
    .sort((a, b) => {
      const dateOrder = b.activityDate.localeCompare(a.activityDate)
      if (dateOrder !== 0) return dateOrder
      const startOrder = (b.activityStartDatetime ?? '').localeCompare(a.activityStartDatetime ?? '')
      if (startOrder !== 0) return startOrder
      return b.stravaActivityId - a.stravaActivityId
    }))

  // Training status is written by the coach, not by this component (#499).
  //
  // It used to be a local `done / due` ratio over the trailing week, which the
  // coach never saw — so when the athlete asked "why am I slightly behind?" the
  // coach had no idea the badge existed and invented a reason. That ratio was
  // also wrong on its own terms: it dropped two-a-days whose ride↔plan match came
  // back `ambiguous`, counted declining an explicitly optional session as a miss,
  // and ignored unplanned work entirely, so it could only ever punish.
  //
  // The backend status pipeline now owns the verdict and the wording, and feeds
  // the very same text into the coach's prompt. Rendering it here is a plain read.
  // From #623 the verdict was the colour of the summary card and its word was
  // for screen readers only. Since the owner's review of 2026-10-24 the card is
  // white like every other, so the word is back on screen, once.
  const trainingStatus = riderAssessment?.trainingStatusLabel
    ? {
        label: riderAssessment.trainingStatusLabel,
        title: riderAssessment.trainingStatusRationale,
      }
    : null

  const isNew = (r: RideMetricPoint): boolean => {
    if (!prevLoginDate) return false
    return r.activityDate >= prevLoginDate
  }
  // Every session on the selected date, not `plan.find(d => d.date === …)` —
  // that keeps only one, which is the two-a-day bug #496 fixed and the redesign
  // reintroduced (#645).
  const selectedSessions = sessionsForDate(trainingPlan, selectedDate)
  const selectedDoneKeys = loggedSessionKeys(selectedSessions, recentRides, selectedDate)
  // The coach gets the next session still to be ridden, so a two-a-day stops
  // handing it the morning session all afternoon.
  const coachContextWorkout = nextUnfinishedSession(
    sessionsForDate(trainingPlan, today),
    loggedSessionKeys(sessionsForDate(trainingPlan, today), recentRides, today)
  )

  const latestRecentRide = recentRides[0] ?? null

  // Edit and delete of an entered activity, shared with the day page (#207).
  const enteredActions = useEnteredActivityActions()
  const editEnteredActivity = async (ride: RideMetricPoint) => {
    const edited = await enteredActions.open(ride)
    if (edited) setEditingActivity(edited)
  }
  const removeEnteredActivity = async (ride: RideMetricPoint) => {
    await enteredActions.remove(ride)
  }
  const latestRideActivityKey = latestRecentRide ? rideActivityKey(latestRecentRide) : ''
  const latestRideActivityId = latestRecentRide ? rideActivityRefreshId(latestRecentRide) : null
  const latestRideSummaryKey = latestRideActivityKey
    ? `${SUMMARY_REFRESH_VERSION}:${latestRideActivityKey}`
    : ''

  // Auto-generate loginSummary once if the user has a riderAssessment but no summary yet,
  // or if the stored summary looks truncated (no bullet points / too short).
  const summaryIncomplete = (s: string | null | undefined) =>
    !s || s.length < 60 || !s.includes('- ')

  useEffect(() => {
    if (!authToken || !riderAssessment || !summaryIncomplete(riderAssessment.loginSummary) || summaryTriggeredRef.current) return
    summaryTriggeredRef.current = true
    setSummaryLoading(true)
    refreshLoginSummary(authToken)
      .then((summary) => {
        if (summary) {
          setRiderAssessment({ ...riderAssessment, loginSummary: summary })
        }
      })
      // Said, not swallowed: the card simply did not show (ai-trainer-ops#272).
      .catch(() => showWarning(t('Your training summary could not be loaded. It tries again on your next visit.')))
      .finally(() => setSummaryLoading(false))
  }, [authToken, riderAssessment, setRiderAssessment, showWarning])

  // Lazily regenerate the status badge when the pipeline has invalidated it (or
  // the athlete never had one). Cheap to skip, so it is fire-and-forget: a
  // failure just leaves the segment out rather than blocking the dashboard.
  useEffect(() => {
    if (!authToken || !riderAssessment || riderAssessment.trainingStatusLabel) return
    if (statusTriggeredRef.current) return
    statusTriggeredRef.current = true
    refreshTrainingStatus(authToken)
      .then((badge) => {
        if (!badge) return
        setRiderAssessment({
          ...riderAssessment,
          trainingStatusLabel: badge.label,
          trainingStatusTone: badge.tone,
          trainingStatusRationale: badge.rationale,
        })
      })
      .catch(() => showWarning(t('Your training status could not be loaded. Reload the page to try again.')))
  }, [authToken, riderAssessment, setRiderAssessment, showWarning])

  useEffect(() => {
    if (!authToken || !riderAssessment || !latestRecentRide || latestRideActivityId == null || !latestRideSummaryKey) return
    // Not before the profile: without it the key would have no account, and the
    // ref below would keep the real one from ever being written (review of #1062).
    if (!account) return
    if (summaryRefreshKeyRef.current === latestRideSummaryKey) return

    // Unavailable storage reads as null; the in-memory guard still prevents loops.
    if (readLocal(accountKey(SUMMARY_REFRESH_KEY, account)) === latestRideSummaryKey) return

    summaryRefreshKeyRef.current = latestRideSummaryKey
    const activityIds = [latestRideActivityId]

    setSummaryLoading(true)
    processPendingFeedbacks(authToken, activityIds)
      .then((summary) => {
        if (summary) {
          setRiderAssessment({ ...riderAssessment, loginSummary: summary })
          writeLocal(accountKey(SUMMARY_REFRESH_KEY, account), latestRideSummaryKey)
        }
      })
      // The summary before the ride stays on the card.
      .catch(() =>
        showWarning(t('Your training summary could not be updated with your latest ride. It tries again on your next visit.')),
      )
      .finally(() => setSummaryLoading(false))
  }, [account, authToken, latestRecentRide, latestRideActivityId, latestRideSummaryKey, riderAssessment, setRiderAssessment, showWarning])

  // One activity row: the ride, what was planned, the match, the legs, and the
  // two questions only the athlete can answer (#574, #580). One text size and
  // grey throughout; the chosen legs rating is the only accent (owner's review,
  // 2026-10-24).
  const renderRide = (ride: RideMetricPoint) => {
    const plan = planForRide(ride, trainingPlan)
    const { score, label: scoreLabel } = rideMatchBadge(ride, plan)
    const sport = t(sportTypeText(ride.sportType))
    return (
      <div key={rideActivityKey(ride)} className="py-2 text-sm">
        {/* Activity row */}
        <div className="flex items-center gap-2">
          <p className="w-24 shrink-0 text-gray-500">
            {parseLocalDate(ride.activityDate).toLocaleDateString(localeTag(language), {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
            })}
          </p>
          <span className="min-w-0 flex-1 truncate text-gray-900">
            <span className={`font-medium ${dotted}`}>{sport}</span>
            <span className={dotted}>
              {enteredActivitySlot(ride) != null ? t('Entered by you') : (ride.activityName ?? t('Activity'))}
            </span>
            {isNew(ride) && <span className={`text-gray-500 ${dotted}`}>{t('new')}</span>}
          </span>
          {ride.weatherTemperatureC != null && (
            <span
              className="flex shrink-0 items-center gap-1 text-gray-500"
              title={t(ride.weatherCondition?.replace(/_/g, ' ') ?? 'Weather')}
            >
              <WeatherIcon condition={ride.weatherCondition} />
              {formatTemperature(ride.weatherTemperatureC)}
            </span>
          )}
          {ride.durationSeconds != null && (
            <span className="shrink-0 text-gray-500">{formatDuration(ride.durationSeconds, language)}</span>
          )}
          {enteredActivitySlot(ride) != null && (
            <button
              type="button"
              onClick={() => void editEnteredActivity(ride)}
              aria-label={t('Edit the {sport} you entered on {date}', { sport, date: ride.activityDate })}
              className={iconButton}
            >
              <Pencil size={13} aria-hidden="true" />
            </button>
          )}
          {enteredActivitySlot(ride) != null && (
            <button
              type="button"
              onClick={() => void removeEnteredActivity(ride)}
              aria-label={t('Delete the {sport} you entered on {date}', { sport, date: ride.activityDate })}
              className={iconDeleteButton}
            >
              <Trash2 size={13} aria-hidden="true" />
            </button>
          )}
        </div>
        {/* Plan comparison row */}
        <div className="flex items-center gap-2 sm:ml-26">
          <span className="shrink-0 text-gray-500">{t('planned:')}</span>
          <span className="min-w-0 flex-1 truncate text-gray-500">
            {plan ? (
              <>
                {planText(plan.title) ?? plan.workoutType}
                {plan.durationMinutes ? ` · ${formatPlanDuration(plan, language)}` : ''}
                {plan.targetPower ? ` · ${plan.targetPower.low}–${plan.targetPower.high}W` : ''}
              </>
            ) : (
              t('No planned workout found')
            )}
          </span>
          {plan && (
            <button
              onClick={() =>
                setPendingCoachMessage(buildMatchCoachPrompt(ride, plan, score, t))
              }
              title={t('Ask coach about this match')}
              className="shrink-0 font-medium text-gray-700 hover:text-gray-900"
            >
              {t(scoreLabel)}
            </button>
          )}
          {authToken && (
            <div
              className="flex shrink-0 items-center"
              role="group"
              aria-label={t('How did your legs feel?')}
            >
              {LEG_FEELINGS.map((feel) => {
                const active = ride.feelLegs === feel.value
                return (
                  <button
                    key={feel.value}
                    type="button"
                    onClick={() => handleSetLegs(ride, feel.value)}
                    title={t('Legs: {feel}', { feel: t(feel.label) })}
                    aria-label={t('Legs felt {feel}', { feel: t(feel.label) })}
                    aria-pressed={active}
                    className={
                      active
                        ? 'inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700'
                        : iconButton
                    }
                  >
                    {/* `aria-hidden`: the button is already labelled "Legs felt Fresh". */}
                    <feel.Icon size={14} aria-hidden="true" />
                  </button>
                )
              })}
            </div>
          )}
        </div>
        {/* The matcher refused to guess which session this was; this is
            where the athlete answers it (#574). Renders nothing for the
            normal, unambiguous ride. */}
        <AmbiguousMatchResolver ride={ride} />
        {/* And the other question the coach cannot answer on its own:
            what this session was, when the data could not say (#580).
            Also renders nothing for a ride that classified cleanly. */}
        <SessionPurposeQuestion ride={ride} />
      </div>
    )
  }
  // A ride the coach needs an answer about stays on the first screen; the rest
  // of the history is one tap away (ai-trainer-ops#68).
  // A ride stays here for the rest of the visit once it was here: answering it
  // must not move the row, or the question's own "thanks" would unmount with
  // it before the athlete sees it.
  rideKeysAwaitingAnswer.current = new Set([
    ...rideKeysAwaitingAnswer.current,
    ...recentRides
      .filter((ride) => ride.planMatchStatus === 'ambiguous' || Boolean(ride.purposeQuestionOpen))
      .map(rideActivityKey),
  ])
  const needsAnswer = (ride: RideMetricPoint) => rideKeysAwaitingAnswer.current.has(rideActivityKey(ride))
  const ridesAwaitingAnswer = recentRides.filter(needsAnswer)
  const otherRides = recentRides.filter((ride) => !needsAnswer(ride))

  return (
    <div className="space-y-5">
      {/* Header. Adding an activity sits here, beside the greeting, instead of
          taking a row of its own above the week (ai-trainer-ops#68). */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-900">
            {/* No name means no comma: "Good morning!", not "Good morning, !" (ai-trainer-ops#40). */}
            {/* No waving hand (ai-trainer-ops#51.7). It was one of the emoji
                the issue names, and there is no lucide icon that reads as a
                greeting — `Hand` is a hand, not a wave. A greeting by name needs
                no decoration, so it is dropped rather than translated. */}
            {firstName ? `${t(greeting())}, ${firstName}!` : `${t(greeting())}!`}
          </h1>
          <p className="text-gray-500 text-sm mt-0.5">{formatLongDate(new Date(), language)}</p>
        </div>
        {/* On a phone the icon alone: the German label left about 150 px of a
            390 px screen for the greeting, which then took three lines
            (ai-trainer-ops#131). From sm up the label alone, like every other
            button. */}
        <div className="flex shrink-0 gap-2">
          {/* A race is what the plan is built toward, and setting one should be
              as easy to find as an activity (ai-trainer-ops#175). */}
          <button
            type="button"
            onClick={() => setAddRaceOpen(true)}
            aria-label={t('Add race')}
            className={`${button} min-h-10 min-w-10 justify-center`}
          >
            <Flag size={15} aria-hidden="true" className="sm:hidden" />
            <span className="hidden sm:inline">{t('Add race')}</span>
          </button>
          <button
            type="button"
            onClick={() => setAddActivityOpen(true)}
            aria-label={t('Add activity')}
            className={`${button} min-h-10 min-w-10 justify-center`}
          >
            <Plus size={15} aria-hidden="true" className="sm:hidden" />
            <span className="hidden sm:inline">{t('Add activity')}</span>
          </button>
        </div>
      </div>
      {addRaceOpen && <AddRaceDialog onClose={() => setAddRaceOpen(false)} />}

      {/* A failed sync was silent: the rides just never came (ai-trainer-ops#95). */}
      {analysisStatus === 'error' && analysisError && (
        <p role="alert" className={note(false)}>
          {/* syncErrorMessage builds one of four fixed sentences (ai-trainer-ops#121). */}
          {t(analysisError)}
        </p>
      )}

      {/* What the athlete came for, before anything else on the page.  Also
          where the week strip below reports: it selects, this re-renders (#634). */}
      <SessionHero
        sessions={selectedSessions}
        date={selectedDate}
        doneKeys={selectedDoneKeys}
        onLog={setLoggingSession}
      />
      {isExpertMode && selectedDate === formatLocalDate(new Date()) && <DayBoardWhy />}
      {/* What a plan without rides rests on (ai-trainer-ops#89). */}
      <ColdStartNote />
      {/* The app rebuilt the plan by itself, and says why (#158, #206). */}
      <PlanRebuildNotice />
      {loggingSession && (
        /* The same form as "Add activity", with this session as the answer to
           "which planned session was it?" (ai-trainer-ops#171). A failed save
           keeps the form, with the error and the save again (#127). */
        <LogSessionDialog
          key={`${loggingSession.date}#${loggingSession.slot ?? 0}`}
          open
          session={loggingSession}
          onLogPlanned={logSession}
          onClose={() => setLoggingSession(null)}
        />
      )}

      {/* The race the athlete trains for stays in view: it renders only when
          one is ahead, and it is the goal, not history (ai-trainer-ops#68). */}
      <SeasonCountdown />

      {/* Anything the plan and the sync do not know about: a ride on a rest
          day, a run instead of the ride, a day the plan never covered (#47).
          Opened from the header button, and mounted only while open so every
          opening starts from an empty form. It asks whether this was a planned
          session of the day, and logs it as that one if so (#170, #171). */}
      {addActivityOpen && (
        <LogSessionDialog open onLogPlanned={logSession} onClose={() => setAddActivityOpen(false)} />
      )}
      {editingActivity && (
        <LogSessionDialog
          /* Closing clears `editingActivity`, so this block unmounts and the
             next entry's form is a fresh mount — that, not this key, is what
             keeps one entry's values out of another's form, and the cancel test
             pins it. The key is kept only so the guarantee survives a refactor
             that renders the dialog unconditionally. */
          key={`${editingActivity.date}#${editingActivity.slot}`}
          open
          editing={editingActivity}
          onClose={() => setEditingActivity(null)}
        />
      )}

      {/* What the days below are for: this week and the ones after it
          (ai-trainer-ops#98). */}
      {trainingPlan.length > 0 && <SeasonPlan />}

      {/* The week is the daily read; the month is a question asked occasionally,
          so it opens over the page instead of sitting under it (#621). */}
      {trainingPlan.length > 0 && (
        <WeekStrip
          plan={trainingPlan}
          selectedDate={selectedDate}
          onSelect={setSelectedDate}
          onShowMore={() => setCalendarOpen(true)}
        />
      )}

      <Modal open={calendarOpen} onClose={() => setCalendarOpen(false)} title={t('Training calendar')}>
        <TrainingCalendar showLoggedActivities />
      </Modal>

      {/* Strava history analysis progress */}
      {hasActivityProgress && (
        <div className={card}>
          <p className={`${cardTitle} mb-2`}>{t('Activity analysis')}</p>
          <div className="h-1.5 w-full rounded-full bg-gray-100">
            <div
              className="h-1.5 rounded-full bg-amber-400 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className={`mt-2 ${note(true)}`}>
            {t('{done} of {total} activities analysed', { done: analyzedActivities, total: importProgress.total })}
          </p>
          {importProgress.status === 'error' && importProgress.error && (
            <p className={`mt-1 ${note(false)}`}>{importProgress.error}</p>
          )}
        </div>
      )}

      {/* Coach Timeline — the recent-training summary opens the conversation as a
          pinned coach entry, then plan updates, recommendations and chat interleave
          in the feed below (#418). Takes up the majority of the remaining space. */}
      <div className="flex flex-col">
        {/* `sr-only`, not deleted (ai-trainer-ops#51.6). The words appeared
            twice, a hand's width apart: here as the section label, and again as
            the card's own title inside `AIChat`.

            Hidden rather than removed because the card's title is a `<span>`,
            so this `<h2>` is the only landmark a screen reader can navigate to
            — and the section holds more than the card, the training-status
            summary sits above it. Deleting the heading would take away the one
            structural handle on that whole region to fix something only sighted
            readers can see.

            `AIChat` keeps its title: it is also used on the day view, where it
            is the only thing naming the card. */}
        <h2 className="sr-only">{t('Coach Timeline')}</h2>

        {(riderAssessment?.loginSummary || summaryLoading || trainingStatus) && (
          <div className={`${card} mb-3`} title={trainingStatus?.title}>
            <h3 className={cardTitle}>{t('Your recent training summary')}</h3>
            {/* The verdict, once, in words (#623; owner's review, 2026-10-24). */}
            {trainingStatus && <p className="text-sm font-medium text-gray-700">{trainingStatus.label}</p>}
            <div className="mt-2 text-sm leading-relaxed text-gray-700">
              {summaryLoading ? (
                <p className="text-gray-500">{t('Preparing your training summary…')}</p>
              ) : !riderAssessment?.loginSummary ? (
                // The card can now be opened by the status alone, before the
                // first summary has been written.
                <p className="text-gray-500">{t('Your first summary lands once a ride has been analysed.')}</p>
              ) : (
                <>
                  {loginSummary?.intro && <p>{loginSummary.intro}</p>}
                  {loginSummary?.bullets.length ? (
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                      {loginSummary.bullets.map((bullet, index) => (
                        <li key={`${bullet.label ?? 'summary'}-${index}`}>
                          {bullet.label && <span className="font-medium text-gray-900">{bullet.label}: </span>}
                          {bullet.text}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="whitespace-pre-wrap">{riderAssessment!.loginSummary}</p>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        <AIChat contextWorkout={coachContextWorkout} className="h-[60vh] min-h-[24rem]" />
      </div>

      {/* A ride the coach needs an answer about is the one piece of history
          that stays on the first screen (ai-trainer-ops#68). */}
      {ridesAwaitingAnswer.length > 0 && (
        <div className={card}>
          <h2 className={cardTitle}>{t('Needs your answer')}</h2>
          <div className="divide-y divide-gray-100">{ridesAwaitingAnswer.map(renderRide)}</div>
        </div>
      )}

      {/* Everything else is one tap away, not stacked under the coach
          (ai-trainer-ops#68; the owner: "the chat is the interface"). A native
          disclosure: no state, keyboard and screen reader work as they are. */}
      {(otherRides.length > 0 || hasScoredPredictions || isExpertMode) && (
      <details className={`group ${card}`}>
        <summary className={`cursor-pointer select-none ${cardTitle}`}>
          {t('More: recent activities and predictions')}
        </summary>
        <div className="mt-3 space-y-5">
          {otherRides.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-900">{t('Recent activities')}</h2>
              <div className="divide-y divide-gray-100">{otherRides.map(renderRide)}</div>
            </div>
          )}

          {/* How often the coach's checkable predictions came true, misses
              included (ai-trainer-ops#16). Renders nothing until one is scored. */}
          <PredictionScoreboard />

          {/* Everything below here is expert mode, gathered in one place so turning
              it on adds a block at the bottom rather than pushing the coach down. */}
          {isExpertMode && (
            <p className="text-sm text-gray-500">
              {t('Consumed tokens')}:{' '}
              <span className="font-medium text-gray-900">{consumedTokens.toLocaleString(localeTag(language))}</span>
            </p>
          )}

          {/* Form and fitness over time: a diagnostic, not a daily read.  #619
              promoted it onto the front page; it belongs behind expert mode. */}
          {isExpertMode && <ProgressionChart />}

          {/* Coach's model of the athlete + its confidences — expert mode only */}
          {isExpertMode && <AthletePerformanceModelCard />}

          {/* Recent plan changes / override analytics — expert mode only (#357) */}
          {isExpertMode && authToken && <PlanChangesPanel authToken={authToken} />}
        </div>
      </details>
      )}
    </div>
  )
}
