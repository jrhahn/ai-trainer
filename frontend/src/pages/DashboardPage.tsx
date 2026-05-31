import { useEffect, useRef, useState } from 'react'
import { format } from 'date-fns'
import {
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Clock,
  Sun,
} from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import { adaptTrainingPlan, refreshLoginSummary } from '../services/ai'
import { formatLocalDate, parseLocalDate } from '../utils/workout'

const PREV_LOGIN_KEY = 'ai_trainer_previous_login'

export function formatDuration(seconds: number | undefined): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m} min`
}

export function formatTemperature(value: number | null | undefined): string {
  if (value == null) return ''
  return `${Math.round(value)}°C`
}

function WeatherIcon({ condition }: { condition?: string | null }) {
  const normalized = condition?.toLowerCase()
  if (normalized === 'clear') return <Sun size={12} className="text-amber-500" />
  if (normalized === 'partly_cloudy') {
    return <CloudSun size={12} className="text-amber-500" />
  }
  if (normalized === 'fog') return <CloudFog size={12} className="text-gray-400" />
  if (normalized === 'rain' || normalized === 'drizzle') {
    return <CloudRain size={12} className="text-blue-500" />
  }
  if (normalized === 'snow') return <CloudSnow size={12} className="text-sky-500" />
  if (normalized === 'thunderstorm') {
    return <CloudLightning size={12} className="text-violet-500" />
  }
  return <Cloud size={12} className="text-gray-400" />
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

/** Returns 0-100 match score, or null when not enough data to compare. */
export function computeMatchScore(
  ride: RideMetricPoint,
  plan: Partial<TrainingDay>
): number | null {
  const actualPower = ride.normalizedPowerW ?? ride.avgPowerW

  if (isRestOrNoTargetPlan(plan)) {
    if (!ride.durationSeconds || actualPower == null || ride.tss == null) return 90
    if (ride.tss <= 30) return 80
    if (ride.tss <= 65) return 55
    return 20
  }

  // Strength/non-power plans: score purely on duration completion (0–100 %).
  if (isStrengthPlan(plan)) {
    if (!ride.durationSeconds || !plan.durationMinutes) return 90
    return Math.min(100, Math.round((ride.durationSeconds / 60 / plan.durationMinutes) * 100))
  }

  const parts: number[] = []

  if (ride.durationSeconds != null && plan.durationMinutes) {
    const ratio = ride.durationSeconds / 60 / plan.durationMinutes
    parts.push(Math.max(0, Math.round(100 - Math.abs(1 - ratio) * 200)))
  }

  if (actualPower != null && plan.targetPower) {
    const { low, high } = plan.targetPower
    if (actualPower >= low && actualPower <= high) {
      parts.push(100)
    } else {
      const edge = actualPower < low ? low : high
      const deviation = Math.abs(actualPower - edge) / edge
      parts.push(Math.max(0, Math.round(100 - deviation * 300)))
    }
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

export function matchScoreBadgeStyle(score: number | null, plan: Partial<TrainingDay>): string {
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
  const snapshot = ride.matchedPlanSnapshot ?? null
  const snapshotDate = typeof snapshot?.date === 'string' ? snapshot.date : null
  const staleMatchedDate = !!ride.matchedPlanDate && ride.matchedPlanDate !== ride.activityDate
  const staleSnapshotDate = !!snapshotDate && snapshotDate !== ride.activityDate

  if (snapshot && !staleMatchedDate && !staleSnapshotDate) {
    return snapshot
  }

  return trainingPlan.find((day) => day.date === ride.activityDate) ?? null
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
    plan.durationMinutes ? `${plan.durationMinutes} min` : null,
    plan.targetPower ? `${plan.targetPower.low}–${plan.targetPower.high}W` : null,
  ].filter(Boolean).join(', ')

  const planLabel = plan.title ?? plan.workoutType ?? 'the planned session'
  const scoreStr = score !== null ? ` The match score came out at ${score}%.` : ''

  return `Just finished "${rideName}" — ${actualParts}. The plan had "${planLabel}" down for ${planParts}.${scoreStr} What's your take — did I execute it well, and anything I should tweak next time?`
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
  const { userProfile, trainingPlan, authToken, stravaConnection, isExpertMode, setTrainingPlan, riderAssessment, setRiderAssessment, rideMetricsHistory, setPendingCoachMessage } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      isExpertMode: s.isExpertMode,
      setTrainingPlan: s.setTrainingPlan,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      rideMetricsHistory: s.rideMetricsHistory,
      setPendingCoachMessage: s.setPendingCoachMessage,
    }))
  )

  const adaptationTriggeredRef = useRef(false)
  const summaryTriggeredRef = useRef(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [prevLoginDate, setPrevLoginDate] = useState<string | null>(null)

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

  // Always show the next 3 upcoming days (today or later)
  const next3Days = trainingPlan.filter((d) => d.date >= today).slice(0, 3)

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

  const recentRides = rideMetricsHistory
    .filter((r) => r.activityDate >= recentWindow && r.activityDate <= today)
    .sort((a, b) => b.activityDate.localeCompare(a.activityDate))

  const isNew = (r: RideMetricPoint): boolean => {
    if (!prevLoginDate) return false
    return r.activityDate >= prevLoginDate
  }

  // If there are past incomplete days the plan is stale — ask the AI coach to
  // reschedule them so the athlete always has current upcoming sessions.
  const hasStalePlan =
    trainingPlan.length > 0 && trainingPlan.some((d) => d.date < today && !d.completed)

  useEffect(() => {
    if (!hasStalePlan) {
      // Reset so a future stale state can trigger adaptation again
      adaptationTriggeredRef.current = false
      return
    }
    if (!authToken || adaptationTriggeredRef.current) return
    adaptationTriggeredRef.current = true
    adaptTrainingPlan([], authToken)
      .then((updatedPlan) => setTrainingPlan(updatedPlan))
      .catch(() => {
        // keep existing plan on error; ref stays true to avoid infinite retries
      })
  }, [hasStalePlan, authToken, setTrainingPlan])

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

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {greeting()}, {userProfile?.name?.split(' ')[0] ?? 'Athlete'}! 👋
        </h1>
        <p className="text-gray-500 text-sm mt-0.5">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
      </div>

      {isExpertMode && (
        <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
            Consumed tokens
          </p>
          <p className="text-2xl font-bold text-gray-900">
            {consumedTokens.toLocaleString()}
          </p>
        </div>
      )}

      {/* Post-login ride summary */}
      {(riderAssessment?.loginSummary || summaryLoading) && (
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
          <p className="text-xs font-semibold text-blue-600 uppercase tracking-wider mb-1">
            📊 Your Recent Training Summary
          </p>
          {summaryLoading ? (
            <p className="text-sm text-blue-400 italic">Preparing your training summary…</p>
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

      {/* Activities: recent rides from last 3 (or up to 7) days + upcoming plan */}
      {(recentRides.length > 0 || next3Days.length > 0) && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Activities</h2>
          <div className="space-y-1.5">
            {recentRides.map((ride) => {
              const plan = planForRide(ride, trainingPlan)
              const score = plan ? computeMatchScore(ride, plan) : null
              const scoreLabel = plan ? matchScoreLabel(score, plan, ride.labelOverride) : '?'
              const scoreBadgeStyle = plan ? matchScoreBadgeStyle(score, plan) : 'bg-gray-100 text-gray-500'
              return (
                <div
                  key={ride.stravaActivityId}
                  className="bg-white rounded-lg border border-gray-100 px-3 py-2"
                >
                  {/* Activity row */}
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-gray-400 flex-shrink-0 w-16">
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
                      <span className="flex items-center gap-1 text-xs text-gray-400 flex-shrink-0">
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
                    <span className="text-xs text-gray-400">planned:</span>
                    <span className="text-xs text-gray-600 font-medium truncate flex-1">
                      {plan ? (
                        <>
                          {plan.title ?? plan.workoutType}
                          {plan.durationMinutes ? ` · ${plan.durationMinutes} min` : ''}
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
                  </div>
                </div>
              )
            })}
            {recentRides.length > 0 && next3Days.length > 0 && (
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider pt-2 pb-0.5 pl-1">
                Upcoming
              </p>
            )}
            {next3Days.map((day) => (
              <WorkoutCard key={day.date} day={day} compact />
            ))}
          </div>
        </div>
      )}

      {/* Ask your coach — takes up the majority of the remaining space */}
      <div className="flex flex-col">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Ask your coach</h2>
        <AIChat
          contextWorkout={trainingPlan.find((d) => d.date === today)}
          className="flex-1 h-[calc(100vh-22rem)] min-h-[24rem] shadow-sm"
        />
      </div>

      {/* Athlete progression charts — expert mode only */}
      {isExpertMode && <ProgressionChart />}
    </div>
  )
}
