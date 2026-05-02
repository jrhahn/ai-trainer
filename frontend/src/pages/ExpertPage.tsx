import { differenceInDays, format } from 'date-fns'
import { useEffect, useRef, useState } from 'react'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent } from '../store/useAppStore'
import TrainingCalendar from '../components/TrainingCalendar'
import FitnessMetricsCard from '../components/FitnessMetricsCard'
import ProgressionChart from '../components/ProgressionChart'
import TrainingLoadChart from '../components/TrainingLoadChart'
import RaceReadinessCard from '../components/RaceReadinessCard'
import FitFileUpload from '../components/FitFileUpload'
import StravaConnect from '../components/StravaConnect'
import { useStravaSync } from '../hooks/useStravaSync'
import { adaptTrainingPlan, fetchRaceEventFeedback, generateTrainingPlan } from '../services/ai'
import { fetchCoachMemory, fetchMetricsHistory, fetchRideMetricsHistory } from '../services/user'

const REFRESH_INTERVAL_MS = 60 * 1000 // refresh all graphs once per minute

export default function ExpertPage() {
  const {
    userProfile,
    trainingPlan,
    raceEvents,
    riderAssessment,
    stravaAnalysisComplete,
    authToken,
    setTrainingPlan,
    setCoachMemory,
    setMetricsHistory,
    setRideMetricsHistory,
  } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      raceEvents: s.raceEvents,
      riderAssessment: s.riderAssessment,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
      authToken: s.authToken,
      setTrainingPlan: s.setTrainingPlan,
      setCoachMemory: s.setCoachMemory,
      setMetricsHistory: s.setMetricsHistory,
      setRideMetricsHistory: s.setRideMetricsHistory,
    }))
  )

  const queryClient = useQueryClient()
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [eventFeedback, setEventFeedback] = useState<string | null>(null)
  const [eventFeedbackLoading, setEventFeedbackLoading] = useState(false)
  const [planQuestion, setPlanQuestion] = useState<string | null>(null)
  const [planUpdateLoading, setPlanUpdateLoading] = useState(false)
  const [eventError, setEventError] = useState<string | null>(null)

  // Periodically refresh metrics data and the readiness score so charts stay
  // current as the date progresses (e.g. at midnight or after background imports).
  useEffect(() => {
    if (!authToken) return
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
  }, [authToken, setMetricsHistory, setRideMetricsHistory, queryClient])

  const { stravaActivities, analysisStatus, analysisError, newRidesCount } = useStravaSync()

  const upcomingRaceDate =
    raceEvents
      .map((event) => event.date)
      .filter((date) => date >= new Date().toISOString().split('T')[0])
      .sort()[0] ?? userProfile?.raceDate

  const daysToRace =
    upcomingRaceDate
      ? differenceInDays(new Date(upcomingRaceDate), new Date())
      : null

  const updatePlanFromCoach = async () => {
    if (!authToken || planUpdateLoading) return
    setPlanUpdateLoading(true)
    setEventError(null)
    try {
      const updatedPlan = trainingPlan.length > 0
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
      // local memory can stay stale until the next full refresh
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
      setEventError(err instanceof Error ? err.message : 'Could not get coach feedback for this race')
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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Expert View</h1>
          <p className="text-gray-500 text-sm mt-0.5">Full training metrics &amp; calendar</p>
        </div>
        {daysToRace !== null && daysToRace >= 0 && (
          <div className="bg-gray-100 text-gray-800 rounded-xl px-4 py-2 text-center">
            <div className="text-2xl font-bold">{daysToRace}</div>
            <div className="text-xs font-medium">days to race</div>
          </div>
        )}
      </div>

      {/* Fitness metrics */}
      <FitnessMetricsCard />

      {/* Athlete progression chart */}
      <ProgressionChart />

      {/* Per-ride training load time series */}
      <TrainingLoadChart />

      {/* Race-day readiness */}
      {(userProfile?.trainingGoal === 'race' || userProfile?.raceDate || raceEvents.length > 0) && (
        <RaceReadinessCard />
      )}

      {/* .fit file upload */}
      <FitFileUpload />

      {/* Strava */}
      <StravaConnect />

      {/* Last ride feedback */}
      {riderAssessment?.lastRideFeedback && (
        <div className="bg-white border border-gray-100 rounded-xl shadow-sm px-4 py-3">
          <p className="text-sm font-semibold text-gray-800 mb-1">🚴 Last Ride Feedback</p>
          <p className="text-sm text-gray-700 leading-relaxed">{riderAssessment.lastRideFeedback}</p>
        </div>
      )}

      {/* Strava analysis status */}
      {newRidesCount > 0 && analysisStatus !== 'analysing' && (
        <div className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
          <RefreshCw size={18} className="text-gray-500 flex-shrink-0" />
          <p className="text-sm font-semibold text-gray-800">
            {newRidesCount === 1 ? '1 new ride' : `${newRidesCount} new rides`} detected on Strava — analysing…
          </p>
        </div>
      )}
      {analysisStatus === 'analysing' && (
        <div className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
          <Loader2 size={18} className="animate-spin text-gray-500 flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold text-gray-800">Analysing your recent rides…</p>
            <p className="text-xs text-gray-500">Assessing your FTP, threshold HR and rider profile to personalise your training plan.</p>
          </div>
        </div>
      )}
      {(analysisStatus === 'done' || stravaAnalysisComplete) && riderAssessment && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
          <p className="text-sm font-semibold text-gray-800 mb-1">🎯 Training plan updated based on your Strava rides</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs text-gray-700">
            <span>🚴 Rider type: <strong className="capitalize">{riderAssessment.riderType}</strong></span>
          </div>
          {riderAssessment.notes && (
            <p className="text-xs text-gray-500 mt-2 italic">{riderAssessment.notes}</p>
          )}
          {riderAssessment.rideInsights && (
            <details className="mt-2">
              <summary className="text-xs font-semibold text-gray-700 cursor-pointer select-none">
                📊 Ride analysis &amp; recommendations ▾
              </summary>
              <p className="text-xs text-gray-600 mt-1 whitespace-pre-wrap">{riderAssessment.rideInsights}</p>
            </details>
          )}
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
            {planUpdateLoading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            Update training plan
          </button>
        </div>
        {(eventFeedbackLoading || eventFeedback || planQuestion || eventError) && (
          <div className="mb-3 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
            {eventFeedbackLoading && (
              <p className="text-sm font-medium text-amber-700">Coach is checking how this race fits…</p>
            )}
            {eventFeedback && (
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">{eventFeedback}</p>
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
    </div>
  )
}
