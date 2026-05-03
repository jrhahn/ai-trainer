import { useEffect, useRef, useState } from 'react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import StravaImportSummary from '../components/StravaImportSummary'
import RideFeedbackForm from '../components/RideFeedbackForm'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import { adaptTrainingPlan, refreshLoginSummary } from '../services/ai'

export default function DashboardPage() {
  const { userProfile, trainingPlan, authToken, stravaConnection, setTrainingPlan, riderAssessment, setRiderAssessment, rideMetricsHistory, setRideMetricsHistory } = useAppStore(
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
    }))
  )

  const adaptationTriggeredRef = useRef(false)
  const summaryTriggeredRef = useRef(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [feedbackRide, setFeedbackRide] = useState<RideMetricPoint | null>(null)
  const importProgress = useImportProgress()

  // keep sync running so analysis status updates remain active
  useStravaSync()

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const today = new Date().toISOString().split('T')[0]
  const analyzedRides = Math.min(importProgress.processed, importProgress.total)
  const hasRideProgress = !!stravaConnection && importProgress.status !== 'idle' && importProgress.total > 0
  const progressPct = hasRideProgress
    ? Math.round((analyzedRides / importProgress.total) * 100)
    : 0

  // Recent rides (last 7 days) that are still missing subjective feedback.
  // date-fns subDays is available; activityDate uses ISO YYYY-MM-DD strings
  // so lexicographic string comparison correctly sorts chronologically.
  const FEEDBACK_WINDOW_DAYS = 7
  const sevenDaysAgo = format(
    new Date(Date.now() - FEEDBACK_WINDOW_DAYS * 24 * 60 * 60 * 1000),
    'yyyy-MM-dd',
  )
  const ridesNeedingFeedback = rideMetricsHistory.filter(
    // ISO YYYY-MM-DD strings compare correctly as plain strings
    (r) => r.activityDate >= sevenDaysAgo && r.activityDate <= today && !r.userNote,
  )

  // Always show the next 3 upcoming days (today or later)
  const next3Days = trainingPlan.filter((d) => d.date >= today).slice(0, 3)

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

  // Auto-generate loginSummary once if the user has a riderAssessment but no summary yet
  useEffect(() => {
    if (!authToken || !riderAssessment || riderAssessment.loginSummary || summaryTriggeredRef.current) return
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

      {/* Post-login ride summary */}
      {(riderAssessment?.loginSummary || summaryLoading) && (
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
          <p className="text-xs font-semibold text-blue-600 uppercase tracking-wider mb-1">
            📊 Your Recent Training Summary
          </p>
          {summaryLoading ? (
            <p className="text-sm text-blue-400 italic">Preparing your training summary…</p>
          ) : (
            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
              {riderAssessment!.loginSummary}
            </p>
          )}
        </div>
      )}

      {/* Strava history analysis progress (rides-level only) */}
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

      {/* Next 3 days */}
      {next3Days.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Next 3 days</h2>
          <div className="space-y-1.5">
            {next3Days.map((day) => (
              <WorkoutCard key={day.date} day={day} compact />
            ))}
          </div>
        </div>
      )}

      {/* Recent rides missing subjective feedback */}
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

      {/* Ask your coach — takes up the majority of the remaining space */}
      <div className="flex flex-col">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Ask your coach</h2>
        <AIChat
          contextWorkout={trainingPlan.find((d) => d.date === today)}
          className="flex-1 h-[calc(100vh-22rem)] min-h-[24rem] shadow-sm"
        />
      </div>

      {/* Athlete progression charts */}
      <ProgressionChart />

      {/* Post-ride feedback modal */}
      {feedbackRide && (
        <RideFeedbackForm
          stravaActivityId={feedbackRide.stravaActivityId}
          activityDate={feedbackRide.activityDate}
          onSaved={(userNote) => {
            // Update the local ride metrics history so the card disappears
            setRideMetricsHistory(
              rideMetricsHistory.map((r) =>
                r.stravaActivityId === feedbackRide.stravaActivityId
                  ? { ...r, userNote }
                  : r,
              ),
            )
            setFeedbackRide(null)
          }}
          onCancel={() => setFeedbackRide(null)}
        />
      )}
    </div>
  )
}
