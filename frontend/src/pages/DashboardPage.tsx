import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { format } from 'date-fns'
import { Bot, CheckCircle2, Clock } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import PlanChangesPanel from '../components/PlanChangesPanel'
import TrainingCalendar from '../components/TrainingCalendar'
import { WeatherIcon } from '../components/WeatherBadge'
import { formatTemperature } from '../utils/weather'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import { processPendingFeedbacks, refreshLoginSummary } from '../services/ai'
import { setRideLegs } from '../services/user'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { effectivePlannedMinutes, formatPlanDuration } from '../utils/planDuration'

const PREV_LOGIN_KEY = 'ai_trainer_previous_login'
const SUMMARY_REFRESH_KEY = 'ai_trainer_summary_refresh_activity_ids'
const SUMMARY_REFRESH_VERSION = 'latest-activity-v6'
const CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO = 0.8
const CONTAINED_DUPLICATE_TIME_TOLERANCE_MS = 10 * 60 * 1000

// Quick leg-freshness ratings tappable directly on each activity card.
const LEG_FEELINGS = [
  { value: 'fresh', emoji: '🟢', label: 'Fresh' },
  { value: 'normal', emoji: '🟡', label: 'Normal' },
  { value: 'heavy', emoji: '🔴', label: 'Heavy' },
] as const

export function formatDuration(seconds: number | undefined): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
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

function isRestOrNoTargetPlan(plan: Partial<TrainingDay>): boolean {
  const workoutType = plan.workoutType?.toLowerCase()
  return workoutType === 'rest' || (!plan.durationMinutes && !plan.targetPower)
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
  const intervalPowerValues = plan.intervals
    ?.map((interval) => interval.power)
    .filter((power) => power > 0) ?? []
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

  if (actualPower != null) {
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

export function matchScoreBadgeStyle(
  score: number | null,
  plan: Partial<TrainingDay>,
  labelOverride?: string | null
): string {
  if (labelOverride) {
    const label = labelOverride.toLowerCase()
    if (label === 'ok') return 'bg-green-100 text-green-700'
    if (label === 'additional' || label === 'extra') {
      return 'bg-blue-100 text-blue-700'
    }
    if (label === 'too much') return 'bg-red-100 text-red-700'
    if (label === 'mismatch') return 'bg-orange-100 text-orange-700'
  }

  if (score === null) return 'bg-gray-100 text-gray-500'

  if (isRestOrNoTargetPlan(plan)) {
    if (score >= 90) return 'bg-green-100 text-green-700'
    if (score >= 75) return 'bg-lime-100 text-lime-700'
    if (score >= 40) return 'bg-amber-100 text-amber-700'
    return 'bg-red-100 text-red-700'
  }

  if (isStrengthPlan(plan)) {
    if (score >= 85) return 'bg-green-100 text-green-700'
    if (score >= 55) return 'bg-amber-100 text-amber-700'
    if (score >= 25) return 'bg-orange-100 text-orange-700'
    return 'bg-red-100 text-red-700'
  }

  return score >= 90
    ? 'bg-green-100 text-green-700'
    : score >= 75
      ? 'bg-emerald-100 text-emerald-700'
      : score >= 60
        ? 'bg-amber-100 text-amber-700'
        : score >= 40
          ? 'bg-orange-100 text-orange-700'
          : 'bg-red-100 text-red-700'
}

export function planForRide(
  ride: RideMetricPoint,
  trainingPlan: TrainingDay[]
): Partial<TrainingDay> | null {
  const currentPlan = trainingPlan.find((day) => day.date === ride.activityDate)
  if (currentPlan) return currentPlan

  const snapshot = ride.matchedPlanSnapshot ?? null
  const snapshotDate = typeof snapshot?.date === 'string' ? snapshot.date : null
  const staleMatchedDate = !!ride.matchedPlanDate && ride.matchedPlanDate !== ride.activityDate
  const staleSnapshotDate = !!snapshotDate && snapshotDate !== ride.activityDate

  if (snapshot && !staleMatchedDate && !staleSnapshotDate) {
    return snapshot
  }

  return null
}


/** Builds a natural, trainer-style prompt asking for feedback on a ride vs plan. */
export function buildMatchCoachPrompt(
  ride: RideMetricPoint,
  plan: Partial<TrainingDay>,
  score: number | null
): string {
  const rideName = ride.activityName ?? 'my ride'
  const actualMin = ride.durationSeconds ? Math.round(ride.durationSeconds / 60) : null
  const actualPower = ride.normalizedPowerW ?? ride.avgPowerW

  const actualParts = [
    actualMin ? `${actualMin} min` : null,
    actualPower ? `${Math.round(actualPower)}W` : null,
  ].filter(Boolean).join(', ')

  const planParts = [
    plan.durationMinutes ? formatPlanDuration(plan) : null,
    plan.targetPower ? `${plan.targetPower.low}–${plan.targetPower.high}W` : null,
  ].filter(Boolean).join(', ')

  const planLabel = plan.title ?? plan.workoutType ?? 'the planned session'
  const label = matchScoreLabel(score, plan, ride.labelOverride)
  const scoreStr = score !== null ? ` with a ${score}% score` : ''
  const labelStr = [
    ` The displayed match label is "${label}"${scoreStr}.`,
    'Treat this as the current app state and do not invent data-quality causes',
    'unless the activity data explicitly shows that.',
  ].join(' ')

  return `Just finished "${rideName}" — ${actualParts}. The plan had "${planLabel}" down for ${planParts}.${labelStr} What's your take — did I execute it well, and anything I should tweak next time?`
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
  const { userProfile, trainingPlan, authToken, stravaConnection, isExpertMode, riderAssessment, setRiderAssessment, rideMetricsHistory, updateRideMetricLegs, setPendingCoachMessage } = useAppStore(
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
    }))
  )

  const summaryTriggeredRef = useRef(false)
  const summaryRefreshKeyRef = useRef<string | null>(null)
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

  useEffect(() => {
    try {
      const stored = localStorage.getItem(PREV_LOGIN_KEY)
      setPrevLoginDate(stored ? formatLocalDate(new Date(stored)) : null)
      localStorage.setItem(PREV_LOGIN_KEY, new Date().toISOString())
    } catch {
      // localStorage may be unavailable in some contexts; silently ignore
    }
  }, [])
  const importProgress = useImportProgress()

  // keep sync running so analysis status updates remain active
  useStravaSync()

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

  const hasTodayActivity = recentRides.some((r) => r.activityDate === today)

  // Always show the next 3 upcoming days, but do not repeat today once an activity
  // has already been logged for today.
  const next3Days = trainingPlan
    .filter((d) => d.date >= today && !(hasTodayActivity && d.date === today))
    .slice(0, 3)

  // "Today's status" glance strip (#417): today / tomorrow / an honest, signal-backed
  // training-status indicator. Each part is omitted when we have no real data for it —
  // the status line in particular is derived from actual plan adherence, never faked.
  const tomorrow = formatLocalDate(new Date(parseLocalDate(today).getTime() + 24 * 60 * 60 * 1000))
  const todayPlan = trainingPlan.find((d) => d.date === today)
  const tomorrowPlan = trainingPlan.find((d) => d.date === tomorrow)

  const sessionLabel = (d: TrainingDay | undefined): string | null => {
    if (!d) return null
    if (d.workoutType === 'rest') return 'Rest'
    return d.title?.trim() || d.workoutType.charAt(0).toUpperCase() + d.workoutType.slice(1)
  }

  let todayStatusText: string | null = null
  if (hasTodayActivity) {
    todayStatusText =
      todayPlan && todayPlan.workoutType !== 'rest'
        ? `${sessionLabel(todayPlan)} completed`
        : 'Session logged'
  } else if (todayPlan) {
    todayStatusText =
      todayPlan.workoutType === 'rest' ? 'Rest day' : `Today: ${sessionLabel(todayPlan)}`
  }

  const tomorrowStatusLabel = sessionLabel(tomorrowPlan)

  // Training status = plan adherence over the trailing 7 days. Only counts planned
  // (non-rest) sessions that already fell due; if none exist we have no trustworthy
  // signal and simply drop the status segment rather than showing a decorative label.
  //
  // A planned session counts as done when a synced activity was matched to that plan
  // day, not just when the athlete filed feedback (`completed`). Feedback is optional,
  // so hinging adherence on it alone reports "Behind plan" for sessions that were
  // demonstrably done and matched — the exact contradiction athletes flag.
  const matchedPlanDates = new Set(
    rideMetricsHistory
      .filter(
        (r) =>
          r.matchedPlanDate != null &&
          (r.planMatchStatus === 'auto_matched' || r.planMatchStatus === 'manual_matched')
      )
      .map((r) => r.matchedPlanDate as string)
  )
  const isPlanDayDone = (d: TrainingDay): boolean =>
    !!d.completed || matchedPlanDates.has(d.date)
  // Past sessions always count as due. Today's session only enters the window once
  // it's actually done — a session still ahead of you in the day must not count
  // against adherence, but a completed/matched one should get its credit.
  const adherenceWindow = trainingPlan.filter(
    (d) =>
      d.date >= sevenDaysAgo &&
      d.workoutType !== 'rest' &&
      (d.date < today || (d.date === today && isPlanDayDone(d)))
  )
  const plannedDue = adherenceWindow.length
  const plannedDone = adherenceWindow.filter(isPlanDayDone).length
  const trainingStatus =
    plannedDue === 0
      ? null
      : plannedDone / plannedDue >= 0.8
        ? { label: 'On track', className: 'text-green-600' }
        : plannedDone / plannedDue >= 0.5
          ? { label: 'Slightly behind', className: 'text-amber-600' }
          : { label: 'Behind plan', className: 'text-red-600' }

  const statusSegments: Array<{ key: string; node: ReactNode }> = []
  if (todayStatusText) {
    statusSegments.push({
      key: 'today',
      node: (
        <span className="flex items-center gap-1.5 font-medium text-gray-800">
          {hasTodayActivity && <CheckCircle2 className="w-4 h-4 text-green-600 flex-shrink-0" />}
          {todayStatusText}
        </span>
      ),
    })
  }
  if (tomorrowStatusLabel) {
    statusSegments.push({
      key: 'tomorrow',
      node: <span className="text-gray-500">Tomorrow: {tomorrowStatusLabel}</span>,
    })
  }
  if (trainingStatus) {
    statusSegments.push({
      key: 'status',
      node: <span className={`font-medium ${trainingStatus.className}`}>{trainingStatus.label}</span>,
    })
  }

  const isNew = (r: RideMetricPoint): boolean => {
    if (!prevLoginDate) return false
    return r.activityDate >= prevLoginDate
  }
  const latestRecentRide = recentRides[0] ?? null
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
      .catch(() => {
        // silently ignore — the card simply won't show
      })
      .finally(() => setSummaryLoading(false))
  }, [authToken, riderAssessment, setRiderAssessment])

  useEffect(() => {
    if (!authToken || !riderAssessment || !latestRecentRide || latestRideActivityId == null || !latestRideSummaryKey) return
    if (summaryRefreshKeyRef.current === latestRideSummaryKey) return

    try {
      if (localStorage.getItem(SUMMARY_REFRESH_KEY) === latestRideSummaryKey) return
    } catch {
      // localStorage may be unavailable; in-memory guard still prevents loops
    }

    summaryRefreshKeyRef.current = latestRideSummaryKey
    const activityIds = [latestRideActivityId]

    setSummaryLoading(true)
    processPendingFeedbacks(authToken, activityIds)
      .then((summary) => {
        if (summary) {
          setRiderAssessment({ ...riderAssessment, loginSummary: summary })
          try {
            localStorage.setItem(SUMMARY_REFRESH_KEY, latestRideSummaryKey)
          } catch {
            // localStorage may be unavailable; ignore after successful refresh
          }
        }
      })
      .catch(() => {
        // silently ignore — the existing summary remains available
      })
      .finally(() => setSummaryLoading(false))
  }, [authToken, latestRecentRide, latestRideActivityId, latestRideSummaryKey, riderAssessment, setRiderAssessment])

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {greeting()}, {userProfile?.name?.split(' ')[0] ?? 'Athlete'}! 👋
        </h1>
        <p className="text-gray-500 text-sm mt-0.5">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
      </div>

      {/* Today's status — compact at-a-glance strip (#417) */}
      {statusSegments.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-white border border-gray-200 rounded-lg px-4 py-2.5 shadow-sm text-sm">
          {statusSegments.map((seg, i) => (
            <Fragment key={seg.key}>
              {i > 0 && <span className="text-gray-300" aria-hidden="true">·</span>}
              {seg.node}
            </Fragment>
          ))}
        </div>
      )}

      {isExpertMode && (
        <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Consumed tokens
          </p>
          <p className="text-2xl font-bold text-gray-900">
            {consumedTokens.toLocaleString()}
          </p>
        </div>
      )}

      {/* Activities: recent rides from last 3 (or up to 7) days + upcoming plan */}
      {(recentRides.length > 0 || next3Days.length > 0) && (
        <div>
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Activities</h2>
          <div className="space-y-1.5">
            {recentRides.map((ride) => {
              const plan = planForRide(ride, trainingPlan)
              const score = plan ? computeMatchScore(ride, plan) : null
              const scoreLabel = plan ? matchScoreLabel(score, plan, ride.labelOverride) : '?'
              const scoreBadgeStyle = plan
                ? matchScoreBadgeStyle(score, plan, ride.labelOverride)
                : 'bg-gray-100 text-gray-500'
              return (
                <div
                  key={rideActivityKey(ride)}
                  className="bg-white rounded-lg border border-gray-100 px-3 py-2"
                >
                  {/* Activity row */}
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-gray-500 flex-shrink-0 w-16">
                      {parseLocalDate(ride.activityDate).toLocaleDateString(undefined, {
                        weekday: 'short',
                        month: 'short',
                        day: 'numeric',
                      })}
                    </p>
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 capitalize flex-shrink-0">
                      {ride.sportType.toLowerCase().replace(/_/g, ' ')}
                    </span>
                    <span className="text-xs text-gray-700 font-medium flex-1 truncate">
                      {ride.activityName ?? 'Activity'}
                    </span>
                    {ride.weatherTemperatureC != null && (
                      <span
                        className="flex items-center gap-1 text-xs text-gray-500 flex-shrink-0"
                        title={ride.weatherCondition?.replace(/_/g, ' ') ?? 'Weather'}
                      >
                        <WeatherIcon condition={ride.weatherCondition} />
                        {formatTemperature(ride.weatherTemperatureC)}
                      </span>
                    )}
                    {ride.durationSeconds != null && (
                      <span className="flex items-center gap-1 text-xs text-gray-500 flex-shrink-0">
                        <Clock size={11} />
                        {formatDuration(ride.durationSeconds)}
                      </span>
                    )}
                    {isNew(ride) && (
                      <span className="text-xs font-semibold px-1.5 py-0.5 rounded bg-green-100 text-green-700 flex-shrink-0">
                        new
                      </span>
                    )}
                  </div>
                  {/* Plan comparison row */}
                  <div className="flex items-center gap-2 mt-1 ml-[4.5rem]">
                    <span className="text-xs text-gray-500">planned:</span>
                    <span className="text-xs text-gray-600 font-medium truncate flex-1">
                      {plan ? (
                        <>
                          {plan.title ?? plan.workoutType}
                          {plan.durationMinutes ? ` · ${formatPlanDuration(plan)}` : ''}
                          {plan.targetPower ? ` · ${plan.targetPower.low}–${plan.targetPower.high}W` : ''}
                        </>
                      ) : (
                        'No planned workout found'
                      )}
                    </span>
                    {plan && (
                      <button
                        onClick={() =>
                          setPendingCoachMessage(buildMatchCoachPrompt(ride, plan, score))
                        }
                        title="Ask coach about this match"
                        className={`text-xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0 hover:opacity-80 transition-opacity ${scoreBadgeStyle}`}
                      >
                        {scoreLabel}
                      </button>
                    )}
                    {authToken && (
                      <div
                        className="flex items-center gap-0.5 flex-shrink-0"
                        role="group"
                        aria-label="How did your legs feel?"
                      >
                        {LEG_FEELINGS.map((feel) => {
                          const active = ride.feelLegs === feel.value
                          return (
                            <button
                              key={feel.value}
                              type="button"
                              onClick={() => handleSetLegs(ride, feel.value)}
                              title={`Legs: ${feel.label}`}
                              aria-label={`Legs felt ${feel.label}`}
                              aria-pressed={active}
                              className={`text-xs leading-none px-1.5 py-1 rounded transition-all ${
                                active
                                  ? 'bg-amber-500 ring-1 ring-amber-500 scale-110'
                                  : 'bg-gray-100 opacity-50 hover:opacity-100 hover:bg-amber-100'
                              }`}
                            >
                              {feel.emoji}
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
            {recentRides.length > 0 && next3Days.length > 0 && (
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider pt-2 pb-0.5 pl-1">
                Upcoming
              </p>
            )}
            {next3Days.map((day) => (
              <WorkoutCard key={day.date} day={day} compact />
            ))}
          </div>
        </div>
      )}

      {/* Strava history analysis progress */}
      {hasActivityProgress && (
        <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-3">
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">
            Strava Activity Analysis
          </p>
          <div className="w-full bg-amber-100 rounded-full h-2.5">
            <div
              className="bg-amber-500 h-2.5 rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="text-sm text-amber-900 mt-2">
            {analyzedActivities} / {importProgress.total} activities analyzed
          </p>
          {importProgress.status === 'error' && importProgress.error && (
            <p className="text-xs text-red-600 mt-1">{importProgress.error}</p>
          )}
        </div>
      )}

      {/* Coach Timeline — the recent-training summary opens the conversation as a
          pinned coach entry, then plan updates, recommendations and chat interleave
          in the feed below (#418). Takes up the majority of the remaining space. */}
      <div className="flex flex-col">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Coach Timeline</h2>

        {(riderAssessment?.loginSummary || summaryLoading) && (
          <div className="bg-white border border-gray-100 rounded-xl shadow-sm px-4 py-3 mb-3">
            <div className="flex items-start gap-2.5">
              <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                <Bot size={14} className="text-amber-600" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                  Your recent training summary
                </p>
                {summaryLoading ? (
                  <p className="text-sm text-gray-500 italic">Preparing your training summary…</p>
                ) : (
                  <div className="text-sm text-gray-700 leading-relaxed">
                    {loginSummary?.intro && <p>{loginSummary.intro}</p>}
                    {loginSummary?.bullets.length ? (
                      <ul className="mt-2 space-y-1 list-disc pl-5">
                        {loginSummary.bullets.map((bullet, index) => (
                          <li key={`${bullet.label ?? 'summary'}-${index}`}>
                            {bullet.label && <span className="font-semibold">{bullet.label}: </span>}
                            {bullet.text}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="whitespace-pre-wrap">{riderAssessment!.loginSummary}</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <AIChat
          contextWorkout={trainingPlan.find((d) => d.date === today)}
          className="h-[60vh] min-h-[24rem] shadow-sm"
        />
      </div>

      {/* Plan-vs-logged month calendar — expert mode only (#369) */}
      {isExpertMode && (
        <div>
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            Training calendar
          </h2>
          <TrainingCalendar showLoggedActivities />
        </div>
      )}

      {/* Recent plan changes / override analytics — expert mode only (#357) */}
      {isExpertMode && authToken && <PlanChangesPanel authToken={authToken} />}

      {/* Athlete progression charts — expert mode only */}
      {isExpertMode && <ProgressionChart />}
    </div>
  )
}
