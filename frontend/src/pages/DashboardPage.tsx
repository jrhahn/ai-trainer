import { differenceInDays, format } from 'date-fns'
import { useEffect, useRef, useState } from 'react'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent, RideMetricPoint, TrainingDay } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import TrainingLoadChart from '../components/TrainingLoadChart'
import RaceReadinessCard from '../components/RaceReadinessCard'
import FitFileUpload from '../components/FitFileUpload'
import StravaConnect from '../components/StravaConnect'
import FitnessMetricsCard from '../components/FitnessMetricsCard'
import TrainingCalendar from '../components/TrainingCalendar'
import StravaImportSummary from '../components/StravaImportSummary'
import RideFeedbackForm from '../components/RideFeedbackForm'
import TodayCard from '../components/TodayCard'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import { useFeedbackDebounce } from '../hooks/useFeedbackDebounce'
import {
  adaptTrainingPlan,
  fetchRaceEventFeedback,
  generateTrainingPlan,
  refreshLoginSummary,
  resolveRideMatch,
} from '../services/ai'
import { fetchCoachMemory, fetchMetricsHistory, fetchRideMetricsHistory } from '../services/user'

const REFRESH_INTERVAL_MS = 60 * 1000

export default function DashboardPage() {
  const {
    userProfile,
    trainingPlan,
    authToken,
    stravaConnection,
    setTrainingPlan,
    riderAssessment,
    setRiderAssessment,
    rideMetricsHistory,
    setRideMetricsHistory,
    raceEvents,
    stravaAnalysisComplete,
    setCoachMemory,
    setMetricsHistory,
    metricsHistory,
    isExpertMode,
  } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      setTrainingPlan: s.setTrainingPlan,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      rideMetricsHistory: s.rideMetricsHistory,
      setRideMetricsHistory: s.setRideMetricsHistory,
      raceEvents: s.raceEvents,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
      setCoachMemory: s.setCoachMemory,
      setMetricsHistory: s.setMetricsHistory,
      metricsHistory: s.metricsHistory,
      isExpertMode: s.isExpertMode,
    }))
  )

  const queryClient = useQueryClient()
  const adaptationTriggeredRef = useRef(false)
  const summaryTriggeredRef = useRef(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [feedbackRide, setFeedbackRide] = useState<RideMetricPoint | null>(null)
  const [eventFeedback, setEventFeedback] = useState<string | null>(null)
  const [eventFeedbackLoading, setEventFeedbackLoading] = useState(false)
  const [planQuestion, setPlanQuestion] = useState<string | null>(null)
  const [planUpdateLoading, setPlanUpdateLoading] = useState(false)
  const [eventError, setEventError] = useState<string | null>(null)

  const importProgress = useImportProgress()
  const { stravaActivities, analysisStatus, analysisError, newRidesCount } = useStravaSync()

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const today = new Date().toISOString().split('T')[0]
  const analyzedRides = Math.min(importProgress.processed, importProgress.total)
  const hasRideProgress =
    !!stravaConnection && importProgress.status !== 'idle' && importProgress.total > 0
  const progressPct = hasRideProgress
    ? Math.round((analyzedRides / importProgress.total) * 100)
    : 0

  const FEEDBACK_WINDOW_DAYS = 7
  const sevenDaysAgo = format(
    new Date(Date.now() - FEEDBACK_WINDOW_DAYS * 24 * 60 * 60 * 1000),
    'yyyy-MM-dd',
  )
  const { isPending: summaryUpdatePending } = useFeedbackDebounce()

  const ridesNeedingFeedback = rideMetricsHistory.filter(
    (r) =>
      r.activityDate >= sevenDaysAgo &&
      r.activityDate <= today &&
      !r.userNote &&
      r.planMatchStatus !== 'ambiguous',
  )

  const ambiguousRideGroups = Object.entries(
    rideMetricsHistory
      .filter((r) => r.planMatchStatus === 'ambiguous' && r.matchedPlanDate)
      .reduce<Record<string, RideMetricPoint[]>>((groups, ride) => {
        const date = ride.matchedPlanDate ?? ride.activityDate
        groups[date] = [...(groups[date] ?? []), ride]
        return groups
      }, {}),
  ).sort(([a], [b]) => a.localeCompare(b))

  const recentlyFeedbackedRides = rideMetricsHistory.filter(
    (r) => r.activityDate >= sevenDaysAgo && r.activityDate <= today && !!r.userNote,
  )

  const next2Days = trainingPlan.filter((d) => d.date > today).slice(0, 2)
  const hasStalePlan =
    trainingPlan.length > 0 && trainingPlan.some((d) => d.date < today && !d.completed)

  const upcomingRaceDate =
    raceEvents
      .map((event) => event.date)
      .filter((date) => date >= today)
      .sort()[0] ?? userProfile?.raceDate

  const daysToRace =
    upcomingRaceDate ? differenceInDays(new Date(upcomingRaceDate), new Date()) : null

  const showConnectNudge = !stravaConnection && metricsHistory.length === 0

  const applyPlanUpdates = (updates?: Partial<TrainingDay>[]) => {
    if (!updates || updates.length === 0) return
    const updatesByDate = Object.fromEntries(updates.filter((u) => u?.date).map((u) => [u.date, u]))
    setTrainingPlan(
      trainingPlan.map((day) =>
        updatesByDate[day.date] ? { ...day, ...updatesByDate[day.date] } : day,
      ),
    )
  }

  const resolveMatchMutation = useMutation({
    mutationFn: ({ plannedDate, stravaActivityId }: { plannedDate: string; stravaActivityId: number }) =>
      resolveRideMatch(authToken!, plannedDate, stravaActivityId),
    onSuccess: async (result) => {
      applyPlanUpdates(result.planUpdates)
      const freshMetrics = await fetchRideMetricsHistory(authToken!)
      setRideMetricsHistory(freshMetrics)
    },
  })

  // Auto-adapt stale plan
  useEffect(() => {
    if (!hasStalePlan) {
      adaptationTriggeredRef.current = false
      return
    }
    if (!authToken || adaptationTriggeredRef.current) return
    adaptationTriggeredRef.current = true
    adaptTrainingPlan([], authToken)
      .then((updatedPlan) => setTrainingPlan(updatedPlan))
      .catch(() => {})
  }, [hasStalePlan, authToken, setTrainingPlan])

  // Auto-generate loginSummary once
  useEffect(() => {
    if (!authToken || !riderAssessment || riderAssessment.loginSummary || summaryTriggeredRef.current) return
    summaryTriggeredRef.current = true
    setSummaryLoading(true)
    refreshLoginSummary(authToken)
      .then((summary) => {
        if (summary) setRiderAssessment({ ...riderAssessment, loginSummary: summary })
      })
      .catch(() => {})
      .finally(() => setSummaryLoading(false))
  }, [authToken, riderAssessment, setRiderAssessment])

  // Expert mode: periodic metrics refresh
  useEffect(() => {
    if (!authToken || !isExpertMode) return
    const refresh = async () => {
      try {
        const [metrics, rideMetrics] = await Promise.all([
          fetchMetricsHistory(authToken),
          fetchRideMetricsHistory(authToken),
        ])
        setMetricsHistory(metrics)
        setRideMetricsHistory(rideMetrics)
        queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
      } catch {
        // silently ignore — stale data is acceptable
      }
    }
    intervalRef.current = setInterval(refresh, REFRESH_INTERVAL_MS)
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current)
    }
  }, [authToken, isExpertMode, setMetricsHistory, setRideMetricsHistory, queryClient])

  const updatePlanFromCoach = async () => {
    if (!authToken || planUpdateLoading) return
    setPlanUpdateLoading(true)
    setEventError(null)
    try {
      const updatedPlan =
        trainingPlan.length > 0
          ? await adaptTrainingPlan([], authToken)
          : await generateTrainingPlan(authToken)
      setTrainingPlan(updatedPlan)
      queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
      setPlanQuestion(null)
      setEventFeedback('Training plan updated with the current race calendar.')
    } catch (err) {
      setEventError(err instanceof Error ? err.message : 'Could not update the training plan')
    } finally {
      setPlanUpdateLoading(false)
    }
  }

  const refreshMemory = async () => {
    if (!authToken) return
    try {
      setCoachMemory(await fetchCoachMemory(authToken))
    } catch {
      // local memory can stay stale
    }
  }

  const handleRaceEventSaved = async (event: RaceEvent, action: 'added' | 'updated') => {
    queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
    void refreshMemory()
    if (!authToken) return
    setEventFeedback(null)
    setEventError(null)
    setPlanQuestion(null)
    setEventFeedbackLoading(true)
    try {
      const feedback = await fetchRaceEventFeedback(event, authToken, action)
      setEventFeedback(feedback)
      setPlanQuestion('Do you want the coach to adapt the training plan for this race?')
    } catch (err) {
      setEventError(
        err instanceof Error ? err.message : 'Could not get coach feedback for this race',
      )
      setPlanQuestion('Do you want the coach to adapt the training plan for this race?')
    } finally {
      setEventFeedbackLoading(false)
    }
  }

  const handleRaceEventRemoved = (event: RaceEvent) => {
    queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
    void refreshMemory()
    setEventFeedback(null)
    setEventError(null)
    setPlanQuestion(`Update the training plan now that the ${event.date} race is removed?`)
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {greeting()}, {userProfile?.name?.split(' ')[0] ?? 'Athlete'}! 👋
          </h1>
          <p className="text-gray-500 text-sm mt-0.5">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
        </div>
        {isExpertMode && daysToRace !== null && daysToRace >= 0 && (
          <div className="bg-gray-100 text-gray-800 rounded-xl px-4 py-2 text-center flex-shrink-0">
            <div className="text-2xl font-bold">{daysToRace}</div>
            <div className="text-xs font-medium">days to race</div>
          </div>
        )}
      </div>

      {/* Today's workout or rest day */}
      <TodayCard today={today} trainingPlan={trainingPlan} />

      {/* Upcoming workouts — next 2 days beyond today */}
      {next2Days.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Coming up
          </h2>
          <div className="space-y-1.5">
            {next2Days.map((day) => (
              <WorkoutCard key={day.date} day={day} compact />
            ))}
          </div>
        </div>
      )}

      {/* Strava ride analysis progress */}
      {hasRideProgress && (
        <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-3">
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">
            Strava Ride Analysis
          </p>
          <div className="w-full bg-amber-100 rounded-full h-2.5">
            <div
              className="bg-amber-500 h-2.5 rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="text-sm text-amber-900 mt-2">
            {importProgress.status === 'done'
              ? `${importProgress.imported} imported, ${importProgress.skipped} skipped`
              : `Processed activities: ${analyzedRides} / ${importProgress.total}`}
          </p>
          {importProgress.status === 'done' && (
            <div className="mt-3">
              <StravaImportSummary progress={importProgress} compact />
            </div>
          )}
          {importProgress.status === 'error' && importProgress.error && (
            <p className="text-xs text-red-600 mt-1">{importProgress.error}</p>
          )}
        </div>
      )}

      {/* Ambiguous same-day ride matches */}
      {ambiguousRideGroups.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Which ride was your planned workout?
          </h2>
          <div className="space-y-3">
            {ambiguousRideGroups.map(([plannedDate, rides]) => {
              const planned = rides[0]?.matchedPlanSnapshot
              return (
                <div key={plannedDate} className="bg-white border border-amber-200 rounded-xl px-4 py-3">
                  <div className="mb-2">
                    <p className="text-sm font-semibold text-gray-900">
                      {plannedDate}
                      {planned?.title ? ` · ${planned.title}` : ''}
                    </p>
                    <p className="text-xs text-gray-500">
                      Multiple rides were imported on this date. Pick the one that should count as the scheduled training ride.
                    </p>
                  </div>
                  <div className="space-y-1.5">
                    {rides.map((ride) => {
                      const startLabel = ride.activityStartDatetime
                        ? format(new Date(ride.activityStartDatetime), 'HH:mm')
                        : null
                      const isPending =
                        resolveMatchMutation.isPending &&
                        resolveMatchMutation.variables?.stravaActivityId === ride.stravaActivityId
                      return (
                        <button
                          key={ride.stravaActivityId}
                          type="button"
                          disabled={resolveMatchMutation.isPending}
                          onClick={() =>
                            resolveMatchMutation.mutate({
                              plannedDate,
                              stravaActivityId: ride.stravaActivityId,
                            })
                          }
                          className="w-full flex items-center justify-between gap-3 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 text-left hover:bg-amber-100 disabled:opacity-60 transition-colors"
                        >
                          <div>
                            <p className="text-sm font-medium text-gray-800">
                              {ride.activityName || ride.sportType}
                              {startLabel ? ` · ${startLabel}` : ''}
                            </p>
                            <p className="text-xs text-gray-500">
                              {ride.durationSeconds ? `${Math.round(ride.durationSeconds / 60)} min` : 'Duration unknown'}
                              {ride.tss ? ` · TSS ${Math.round(ride.tss)}` : ''}
                              {ride.normalizedPowerW ? ` · NP ${ride.normalizedPowerW}W` : ride.avgPowerW ? ` · ${ride.avgPowerW}W avg` : ''}
                            </p>
                          </div>
                          <span className="text-xs font-semibold text-amber-700 bg-white border border-amber-200 px-2 py-1 rounded-lg">
                            {isPending ? 'Saving...' : 'Choose'}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  {resolveMatchMutation.isError && (
                    <p className="text-xs text-red-600 mt-2">
                      Could not save that match. Please try again.
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Recent rides missing feedback */}
      {ridesNeedingFeedback.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            📝 Recent rides — add your feedback
          </h2>
          <div className="space-y-1.5">
            {ridesNeedingFeedback.map((ride) => (
              <button
                key={ride.stravaActivityId}
                onClick={() => setFeedbackRide(ride)}
                className="w-full flex items-center justify-between bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-left hover:bg-amber-100 transition-colors"
              >
                <div>
                  <p className="text-sm font-medium text-gray-800">{ride.activityDate}</p>
                  <p className="text-xs text-gray-500">
                    {ride.sportType}
                    {ride.durationSeconds ? ` · ${Math.round(ride.durationSeconds / 60)} min` : ''}
                    {ride.tss ? ` · TSS ${Math.round(ride.tss)}` : ''}
                  </p>
                </div>
                <span className="text-xs font-semibold text-amber-600 bg-amber-100 px-2 py-1 rounded-lg">
                  + Feedback
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Coach insight */}
      {(riderAssessment?.loginSummary || summaryLoading || summaryUpdatePending || recentlyFeedbackedRides.length > 0) && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Your recent training
          </h2>
          <div className="bg-white border border-gray-100 rounded-xl shadow-sm px-4 py-3 space-y-3">
            {summaryLoading ? (
              <p className="text-sm text-gray-400 italic">Preparing your training summary…</p>
            ) : (
              <>
                {summaryUpdatePending && (
                  <div className="flex items-center gap-1.5 text-xs text-gray-500">
                    <Loader2 size={12} className="animate-spin" />
                    Updating summary with recent feedback…
                  </div>
                )}
                {riderAssessment?.loginSummary && (
                  <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
                    {riderAssessment.loginSummary}
                  </p>
                )}
                {recentlyFeedbackedRides.length > 0 && (
                  <div className="border-t border-gray-100 pt-3 space-y-2">
                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                      Your recent notes
                    </p>
                    <div className="space-y-1.5">
                      {recentlyFeedbackedRides.map((ride) => (
                        <div
                          key={ride.stravaActivityId}
                          className="flex flex-col gap-0.5 text-xs text-gray-600 sm:flex-row sm:items-start sm:gap-2"
                        >
                          <span className="font-medium text-gray-700 sm:w-24 sm:flex-shrink-0">
                            {format(new Date(ride.activityDate), 'EEE MMM d')}
                          </span>
                          <span className="text-gray-500">
                            {ride.sportType}
                            {ride.userNote ? ` · ${ride.userNote}` : ''}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Strava connect nudge — only when not connected, no metrics, and expert OFF */}
      {showConnectNudge && !isExpertMode && (
        <div className="bg-orange-50 border border-orange-200 rounded-xl px-4 py-4">
          <p className="text-sm font-semibold text-orange-800 mb-1">
            Connect Strava to unlock insights
          </p>
          <p className="text-xs text-orange-600 mb-3">
            Import your ride history for personalised coaching, FTP estimation and progression
            tracking.
          </p>
          <StravaConnect />
        </div>
      )}

      {/* AI Coach Chat */}
      <div className="flex flex-col">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Ask your coach
        </h2>
        <AIChat
          contextWorkout={trainingPlan.find((d) => d.date === today)}
          className="flex-1 h-[calc(100vh-22rem)] min-h-[24rem] shadow-sm"
        />
      </div>

      {/* ─── Expert sections ─── */}
      {isExpertMode && (
        <div className="space-y-6 border-t-2 border-dashed border-amber-200 pt-6">
          <p className="text-xs font-semibold text-amber-600 uppercase tracking-wider">
            Expert Mode
          </p>

          {/* Fitness metrics */}
          <FitnessMetricsCard />

          {/* Athlete progression chart */}
          <ProgressionChart />

          {/* Per-ride training load */}
          <TrainingLoadChart />

          {/* Race readiness */}
          {(userProfile?.trainingGoal === 'race' ||
            userProfile?.raceDate ||
            raceEvents.length > 0) && <RaceReadinessCard />}

          {/* Last ride feedback */}
          {riderAssessment?.lastRideFeedback && (
            <div className="bg-white border border-gray-100 rounded-xl shadow-sm px-4 py-3">
              <p className="text-sm font-semibold text-gray-800 mb-1">🚴 Last Ride Feedback</p>
              <p className="text-sm text-gray-700 leading-relaxed">
                {riderAssessment.lastRideFeedback}
              </p>
            </div>
          )}

          {/* Strava analysis status */}
          {newRidesCount > 0 && analysisStatus !== 'analysing' && (
            <div className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
              <RefreshCw size={18} className="text-gray-500 flex-shrink-0" />
              <p className="text-sm font-semibold text-gray-800">
                {newRidesCount === 1 ? '1 new ride' : `${newRidesCount} new rides`} detected on
                Strava — analysing…
              </p>
            </div>
          )}
          {analysisStatus === 'analysing' && (
            <div className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
              <Loader2 size={18} className="animate-spin text-gray-500 flex-shrink-0" />
              <div>
                <p className="text-sm font-semibold text-gray-800">
                  Analysing your recent rides…
                </p>
                <p className="text-xs text-gray-500">
                  Assessing your FTP, threshold HR and rider profile to personalise your training
                  plan.
                </p>
              </div>
            </div>
          )}
          {(analysisStatus === 'done' || stravaAnalysisComplete) && riderAssessment && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
              <p className="text-sm font-semibold text-gray-800 mb-1">
                🎯 Training plan updated based on your Strava rides
              </p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs text-gray-700">
                <span>
                  🚴 Rider type:{' '}
                  <strong className="capitalize">{riderAssessment.riderType}</strong>
                </span>
              </div>
              {riderAssessment.notes && (
                <p className="text-xs text-gray-500 mt-2 italic">{riderAssessment.notes}</p>
              )}
              {riderAssessment.rideInsights &&
                (() => {
                  type RideInsight = {
                    id?: number
                    name?: string
                    note?: string
                    comment?: string
                  }
                  let parsedInsights: RideInsight[] | null = null
                  try {
                    const parsed: unknown = JSON.parse(riderAssessment.rideInsights!)
                    if (
                      Array.isArray(parsed) &&
                      parsed.length > 0 &&
                      typeof parsed[0] === 'object' &&
                      parsed[0] !== null
                    ) {
                      parsedInsights = parsed as RideInsight[]
                    }
                  } catch {
                    // Not JSON — fall through to plain-text rendering
                  }
                  return (
                    <details className="mt-2">
                      <summary className="text-xs font-semibold text-gray-700 cursor-pointer select-none">
                        📊 Ride analysis &amp; recommendations ▾
                      </summary>
                      {parsedInsights ? (
                        <div className="mt-2 space-y-2">
                          {parsedInsights.map((insight, i) => (
                            <div
                              key={insight.id ?? i}
                              className="bg-white border border-gray-100 rounded-lg px-3 py-2"
                            >
                              {insight.name && (
                                <p className="text-xs font-semibold text-gray-700 mb-1">
                                  🚴 {insight.name}
                                </p>
                              )}
                              <p className="text-xs text-gray-600 leading-relaxed">
                                {insight.note ?? insight.comment ?? ''}
                              </p>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-gray-600 mt-1 whitespace-pre-wrap">
                          {riderAssessment.rideInsights}
                        </p>
                      )}
                    </details>
                  )
                })()}
            </div>
          )}
          {analysisStatus === 'error' && (
            <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3">
              <p className="text-sm font-semibold text-red-800">Ride analysis failed</p>
              <p className="text-xs text-red-600">{analysisError}</p>
            </div>
          )}

          {/* Training Calendar */}
          <div>
            <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="text-base font-bold text-gray-800">Training Calendar</h2>
              <button
                type="button"
                onClick={() => void updatePlanFromCoach()}
                disabled={!authToken || planUpdateLoading}
                className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
              >
                {planUpdateLoading ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <RefreshCw size={16} />
                )}
                Update training plan
              </button>
            </div>
            {(eventFeedbackLoading || eventFeedback || planQuestion || eventError) && (
              <div className="mb-3 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
                {eventFeedbackLoading && (
                  <p className="text-sm font-medium text-amber-700">
                    Coach is checking how this race fits…
                  </p>
                )}
                {eventFeedback && (
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
                    {eventFeedback}
                  </p>
                )}
                {eventError && (
                  <p className="text-sm font-medium text-red-600">{eventError}</p>
                )}
                {planQuestion && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <p className="mr-2 text-sm font-semibold text-gray-800">{planQuestion}</p>
                    <button
                      type="button"
                      onClick={() => void updatePlanFromCoach()}
                      disabled={planUpdateLoading}
                      className="rounded-lg bg-gray-900 px-3 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:opacity-50"
                    >
                      Yes
                    </button>
                    <button
                      type="button"
                      onClick={() => setPlanQuestion(null)}
                      disabled={planUpdateLoading}
                      className="rounded-lg px-3 py-2 text-sm font-semibold text-gray-600 hover:bg-amber-100 disabled:opacity-50"
                    >
                      No
                    </button>
                  </div>
                )}
              </div>
            )}
            <TrainingCalendar
              editableEvents
              onRaceEventAdded={(event) => void handleRaceEventSaved(event, 'added')}
              onRaceEventUpdated={(event) => void handleRaceEventSaved(event, 'updated')}
              onRaceEventRemoved={handleRaceEventRemoved}
            />
          </div>

          {/* Recent Strava activities */}
          {stravaActivities.length > 0 && (
            <div>
              <h2 className="text-base font-bold text-gray-800 mb-2">Recent Strava Activities</h2>
              <div className="space-y-2">
                {stravaActivities.slice(0, 5).map((act) => (
                  <div
                    key={act.id}
                    className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex items-center gap-4"
                  >
                    <div className="w-9 h-9 rounded-lg bg-gray-50 flex items-center justify-center">
                      <Activity size={18} className="text-gray-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-semibold text-sm text-gray-900 truncate">{act.name}</p>
                      <p className="text-xs text-gray-500">
                        {act.type} · {(act.distance / 1000).toFixed(1)} km ·{' '}
                        {Math.round(act.moving_time / 60)} min
                        {act.average_watts ? ` · ${Math.round(act.average_watts)}W avg` : ''}
                      </p>
                    </div>
                    <p className="text-xs text-gray-400 flex-shrink-0">
                      {format(new Date(act.start_date), 'MMM d')}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* .fit file upload */}
          <FitFileUpload />

          {/* Strava connect / disconnect */}
          <StravaConnect />
        </div>
      )}

      {/* Post-ride feedback modal */}
      {feedbackRide && (
        <RideFeedbackForm
          stravaActivityId={feedbackRide.stravaActivityId}
          activityDate={feedbackRide.activityDate}
          onSaved={(data) => {
            applyPlanUpdates(data.planUpdates)
            if (data.ride) {
              setRideMetricsHistory(
                rideMetricsHistory.map((r) =>
                  r.stravaActivityId === data.ride?.stravaActivityId ? data.ride : r,
                ),
              )
            } else {
              setRideMetricsHistory(
                rideMetricsHistory.map((r) =>
                  r.stravaActivityId === feedbackRide.stravaActivityId
                    ? { ...r, userNote: data.userNote, coachNote: data.coachNote ?? r.coachNote }
                    : r,
                ),
              )
            }
            setFeedbackRide(null)
          }}
          onCancel={() => setFeedbackRide(null)}
        />
      )}
    </div>
  )
}
