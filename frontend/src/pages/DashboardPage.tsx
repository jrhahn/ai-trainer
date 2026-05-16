import { useEffect, useRef, useState } from 'react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import ProgressionChart from '../components/ProgressionChart'
import { useStravaSync } from '../hooks/useStravaSync'
import { useImportProgress } from '../hooks/useImportProgress'
import { adaptTrainingPlan, refreshLoginSummary } from '../services/ai'

export default function DashboardPage() {
  const { userProfile, trainingPlan, authToken, stravaConnection, setTrainingPlan, riderAssessment, setRiderAssessment } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      setTrainingPlan: s.setTrainingPlan,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
    }))
  )

  const adaptationTriggeredRef = useRef(false)
  const summaryTriggeredRef = useRef(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
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
            {analyzedRides} / {importProgress.total} rides analyzed
          </p>
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
    </div>
  )
}
