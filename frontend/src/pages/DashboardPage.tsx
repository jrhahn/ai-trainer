import { differenceInDays, format } from 'date-fns'
import { Calendar, Clock, Trophy, TrendingUp, Activity, Loader2, RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import TrainingCalendar from '../components/TrainingCalendar'
import WorkoutCard from '../components/WorkoutCard'
import StravaConnect from '../components/StravaConnect'
import AIChat from '../components/AIChat'
import FitnessMetricsCard from '../components/FitnessMetricsCard'
import ProgressionChart from '../components/ProgressionChart'
import RaceReadinessCard from '../components/RaceReadinessCard'
import { useStravaSync } from '../hooks/useStravaSync'

export default function DashboardPage() {
  const { userProfile, trainingPlan, riderAssessment, stravaAnalysisComplete } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      riderAssessment: s.riderAssessment,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
    }))
  )

  const { stravaActivities, analysisStatus, analysisError, newRidesCount } = useStravaSync()

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const daysToRace =
    userProfile?.trainingGoal === 'race' && userProfile.raceDate
      ? differenceInDays(new Date(userProfile.raceDate), new Date())
      : null

  const today = new Date().toISOString().split('T')[0]
  const todayWorkout = trainingPlan.find((d) => d.date === today)
  const weekPlan = trainingPlan.slice(0, 7)
  const weekWorkouts = weekPlan.filter((d) => d.workoutType !== 'rest')
  const weekHours = weekPlan.reduce((sum, d) => sum + d.durationMinutes, 0) / 60

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {greeting()}, {userProfile?.name?.split(' ')[0] ?? 'Athlete'}! 👋
          </h1>
          <p className="text-gray-500 text-sm mt-0.5">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
        </div>
        {daysToRace !== null && daysToRace >= 0 && (
          <div className="bg-purple-100 text-purple-800 rounded-xl px-4 py-2 text-center">
            <div className="text-2xl font-bold">{daysToRace}</div>
            <div className="text-xs font-medium">days to race</div>
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          {
            icon: Calendar,
            label: 'This Week',
            value: `${weekWorkouts.length} workouts`,
            color: 'text-blue-600 bg-blue-50',
          },
          {
            icon: Clock,
            label: 'Hours Planned',
            value: `${weekHours.toFixed(1)}h`,
            color: 'text-amber-600 bg-amber-50',
          },
          {
            icon: Trophy,
            label: 'Completed',
            value: `${trainingPlan.filter((d) => d.completed).length} workouts`,
            color: 'text-green-600 bg-green-50',
          },
          {
            icon: TrendingUp,
            label: 'FTP',
            value: userProfile?.currentFTP ? `${userProfile.currentFTP}W` : 'N/A',
            color: 'text-purple-600 bg-purple-50',
          },
        ].map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center mb-2 ${color}`}>
              <Icon size={18} />
            </div>
            <p className="text-xs text-gray-500">{label}</p>
            <p className="font-bold text-gray-900 text-sm">{value}</p>
          </div>
        ))}
      </div>

      {/* Fitness metrics */}
      <FitnessMetricsCard />

      {/* Athlete progression chart */}
      <ProgressionChart />

      {/* Race-day readiness — shown when training for a race or a race date is set */}
      {(userProfile?.trainingGoal === 'race' || userProfile?.raceDate) && (
        <RaceReadinessCard />
      )}

      {/* Strava */}
      <StravaConnect />

      {/* Last ride feedback — shown whenever stored, updated on each new ride */}
      {riderAssessment?.lastRideFeedback && (
        <div className="bg-white border border-blue-100 rounded-xl shadow-sm px-4 py-3">
          <p className="text-sm font-semibold text-blue-800 mb-1">🚴 Last Ride Feedback</p>
          <p className="text-sm text-gray-700 leading-relaxed">{riderAssessment.lastRideFeedback}</p>
        </div>
      )}

      {/* Strava analysis status */}
      {newRidesCount > 0 && analysisStatus !== 'analysing' && (
        <div className="flex items-center gap-3 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
          <RefreshCw size={18} className="text-green-500 flex-shrink-0" />
          <p className="text-sm font-semibold text-green-800">
            {newRidesCount === 1 ? '1 new ride' : `${newRidesCount} new rides`} detected on Strava — analysing…
          </p>
        </div>
      )}
      {analysisStatus === 'analysing' && (
        <div className="flex items-center gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <Loader2 size={18} className="animate-spin text-blue-500 flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold text-blue-800">Analysing your recent rides…</p>
            <p className="text-xs text-blue-600">Assessing your FTP, threshold HR and rider profile to personalise your training plan.</p>
          </div>
        </div>
      )}
      {(analysisStatus === 'done' || stravaAnalysisComplete) && riderAssessment && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <p className="text-sm font-semibold text-amber-800 mb-1">🎯 Training plan updated based on your Strava rides</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs text-amber-700">
            {riderAssessment.estimatedFTP && (
              <span>⚡ Est. FTP: <strong>{riderAssessment.estimatedFTP} W</strong></span>
            )}
            {riderAssessment.estimatedThresholdHR && (
              <span>❤️ Threshold HR: <strong>{riderAssessment.estimatedThresholdHR} bpm</strong></span>
            )}
            <span>🚴 Rider type: <strong className="capitalize">{riderAssessment.riderType}</strong></span>
          </div>
          {riderAssessment.notes && (
            <p className="text-xs text-amber-600 mt-2 italic">{riderAssessment.notes}</p>
          )}
          {riderAssessment.rideInsights && (
            <details className="mt-2">
              <summary className="text-xs font-semibold text-amber-800 cursor-pointer select-none">
                📊 Ride analysis &amp; recommendations ▾
              </summary>
              <p className="text-xs text-amber-700 mt-1 whitespace-pre-wrap">{riderAssessment.rideInsights}</p>
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

      {/* Today's workout */}
      {todayWorkout && (
        <div>
          <h2 className="text-base font-bold text-gray-800 mb-2">Today's Workout</h2>
          <WorkoutCard day={todayWorkout} />
        </div>
      )}

      {/* Calendar */}
      <div>
        <h2 className="text-base font-bold text-gray-800 mb-2">Training Calendar</h2>
        <TrainingCalendar />
      </div>

      {/* AI Coach Chat */}
      <div>
        <h2 className="text-base font-bold text-gray-800 mb-2">Coach Chat</h2>
        <AIChat />
      </div>

      {/* Strava activities */}
      {stravaActivities.length > 0 && (
        <div>
          <h2 className="text-base font-bold text-gray-800 mb-2">Recent Strava Activities</h2>
          <div className="space-y-2">
            {stravaActivities.slice(0, 5).map((act) => (
              <div
                key={act.id}
                className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex items-center gap-4"
              >
                <div className="w-9 h-9 rounded-lg bg-orange-50 flex items-center justify-center">
                  <Activity size={18} className="text-orange-500" />
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
