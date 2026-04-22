import { differenceInDays, format } from 'date-fns'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import TrainingCalendar from '../components/TrainingCalendar'
import FitnessMetricsCard from '../components/FitnessMetricsCard'
import ProgressionChart from '../components/ProgressionChart'
import TrainingLoadChart from '../components/TrainingLoadChart'
import RaceReadinessCard from '../components/RaceReadinessCard'
import FitFileUpload from '../components/FitFileUpload'
import StravaConnect from '../components/StravaConnect'
import { useStravaSync } from '../hooks/useStravaSync'

export default function ExpertPage() {
  const { userProfile, riderAssessment, stravaAnalysisComplete } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      riderAssessment: s.riderAssessment,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
    }))
  )

  const { stravaActivities, analysisStatus, analysisError, newRidesCount } = useStravaSync()

  const daysToRace =
    userProfile?.trainingGoal === 'race' && userProfile.raceDate
      ? differenceInDays(new Date(userProfile.raceDate), new Date())
      : null

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
      {(userProfile?.trainingGoal === 'race' || userProfile?.raceDate) && (
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
            {riderAssessment.estimatedFTP && (
              <span>⚡ Est. FTP: <strong>{riderAssessment.estimatedFTP} W</strong></span>
            )}
            {riderAssessment.estimatedThresholdHR && (
              <span>❤️ Threshold HR: <strong>{riderAssessment.estimatedThresholdHR} bpm</strong></span>
            )}
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
        <h2 className="text-base font-bold text-gray-800 mb-2">Training Calendar</h2>
        <TrainingCalendar />
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
