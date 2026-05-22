import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Clock, Zap, Heart, CheckCircle, BarChart2, Bot, Loader2, Target, ListChecks, RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import WorkoutFeedbackForm from '../components/WorkoutFeedbackForm'
import AIChat from '../components/AIChat'
import { rateCompletedWorkout, type WorkoutRatingResult } from '../services/ai'
import { saveTrainingPlan, saveWorkoutLog } from '../services/user'
import { parseLocalDate } from '../utils/workout'
import type { WorkoutFeedback, TrainingDay, StravaActivity } from '../store/useAppStore'

const typeColors: Record<string, string> = {
  rest: 'bg-gray-100 text-gray-600',
  endurance: 'bg-blue-100 text-blue-700',
  intervals: 'bg-red-100 text-red-700',
  tempo: 'bg-orange-100 text-orange-700',
  race: 'bg-purple-100 text-purple-700',
  recovery: 'bg-green-100 text-green-700',
  strength: 'bg-teal-100 text-teal-700',
}

function stravaActivityType(activity: StravaActivity): string {
  return (activity.sport_type || activity.type || '').toLowerCase()
}

function plannedWorkoutMatchesActivity(day: TrainingDay, activity: StravaActivity): boolean {
  const type = stravaActivityType(activity)
  if (day.workoutType === 'strength') {
    return type.includes('weight') || type.includes('strength') || type.includes('workout')
  }
  return type === 'cycling' || type.includes('ride')
}

export default function WorkoutPage() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { authToken, trainingPlan, logWorkout, userProfile, updateTrainingDay } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      trainingPlan: s.trainingPlan,
      logWorkout: s.logWorkout,
      userProfile: s.userProfile,
      updateTrainingDay: s.updateTrainingDay,
    }))
  )
  const [showForm, setShowForm] = useState(false)

  const rateWorkoutMutation = useMutation({
    mutationFn: ({ dayWithFeedback }: { dayWithFeedback: TrainingDay }) => {
      // Try to find a Strava activity that matches this workout date so the
      // backend can fetch its streams for precise planned-vs-actual analysis.
      const cachedActivities =
        queryClient.getQueryData<StravaActivity[]>(['stravaActivities', authToken]) ?? []
      const matchingActivity = cachedActivities.find((a) =>
        (a.start_date_local || a.start_date).startsWith(dayWithFeedback.date) &&
        plannedWorkoutMatchesActivity(dayWithFeedback, a)
      )
      return rateCompletedWorkout(dayWithFeedback, authToken!, matchingActivity?.id)
    },
    onSuccess: async (rating: WorkoutRatingResult) => {
      if (rating.feedback) {
        updateTrainingDay(day!.date, { coachFeedback: rating.feedback })
        await saveTrainingPlan(authToken!, useAppStore.getState().trainingPlan)
      }
    },
  })

  const day = trainingPlan.find((d) => d.date === date)

  if (!day) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-500">Workout not found.</p>
        <button onClick={() => navigate('/')} className="mt-4 text-amber-600 hover:underline text-sm">
          ← Back to Dashboard
        </button>
      </div>
    )
  }

  const handleFeedback = async (feedback: WorkoutFeedback) => {
    logWorkout(day.date, feedback)
    setShowForm(false)

    if (authToken) {
      try {
        await saveWorkoutLog(authToken, day.date, feedback)
      } catch {
        // keep optimistic local state even if the network fails
      }
    }

    if (authToken && userProfile) {
      const dayWithFeedback = { ...day, completed: true, feedback }
      rateWorkoutMutation.mutate({ dayWithFeedback })
    }
  }

  const effortLabels: Record<number, string> = {
    1: 'Easy', 2: 'Moderate', 3: 'Hard', 4: 'Very Hard', 5: 'Max',
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <button
        onClick={() => navigate('/')}
        className="flex items-center gap-1.5 text-gray-500 hover:text-gray-800 text-sm"
      >
        <ArrowLeft size={16} />
        Back to Dashboard
      </button>

      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <span
              className={`inline-block text-xs font-semibold px-2.5 py-1 rounded-full capitalize mb-2 ${
                typeColors[day.workoutType] ?? 'bg-gray-100 text-gray-600'
              }`}
            >
              {day.workoutType}
            </span>
            <h1 className="text-xl font-bold text-gray-900">{day.title}</h1>
            <p className="text-sm text-gray-500 mt-0.5">
              {parseLocalDate(day.date).toLocaleDateString(undefined, {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric',
              })}
            </p>
          </div>
          {day.completed && (
            <div className="flex items-center gap-1 text-green-600 text-sm font-medium">
              <CheckCircle size={18} />
              Done
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-4 mb-4">
          <div className="flex items-center gap-1.5 text-sm text-gray-600">
            <Clock size={16} className="text-amber-500" />
            {day.durationMinutes} minutes
          </div>
          {day.targetPower && (
            <div className="flex items-center gap-1.5 text-sm text-gray-600">
              <Zap size={16} className="text-amber-500" />
              {day.targetPower.low}–{day.targetPower.high}W
            </div>
          )}
          {day.targetHeartRate && (
            <div className="flex items-center gap-1.5 text-sm text-gray-600">
              <Heart size={16} className="text-red-400" />
              {day.targetHeartRate.low}–{day.targetHeartRate.high} bpm
            </div>
          )}
        </div>

        <p className="text-gray-700 text-sm leading-relaxed whitespace-pre-wrap">{day.description}</p>

        {/* Workout Purpose */}
        {day.workoutPurpose && (
          <div className="mt-4 bg-amber-50 rounded-xl p-4">
            <h3 className="font-semibold text-sm text-amber-800 mb-1.5 flex items-center gap-1.5">
              <Target size={14} />
              Why this workout
            </h3>
            <p className="text-sm text-amber-900 leading-relaxed">{day.workoutPurpose}</p>
          </div>
        )}

        {/* Key Focus Points */}
        {day.keyFocusPoints && day.keyFocusPoints.length > 0 && (
          <div className="mt-3 bg-blue-50 rounded-xl p-4">
            <h3 className="font-semibold text-sm text-blue-800 mb-1.5 flex items-center gap-1.5">
              <ListChecks size={14} />
              Key Focus Points
            </h3>
            <ul className="space-y-1">
              {day.keyFocusPoints.map((point, i) => (
                <li key={i} className="text-sm text-blue-900 flex items-start gap-2">
                  <span className="mt-1 shrink-0 w-1.5 h-1.5 rounded-full bg-blue-400" />
                  {point}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Nudge to regenerate if coaching details are missing */}
        {!day.workoutPurpose && day.workoutType !== 'rest' && (
          <div className="mt-4 flex items-start gap-3 bg-gray-50 rounded-xl p-4 border border-gray-200">
            <RefreshCw size={16} className="text-gray-400 mt-0.5 shrink-0" />
            <p className="text-xs text-gray-500 leading-relaxed">
              This workout was created before detailed coaching cues were added.{' '}
              <button
                onClick={() => navigate('/')}
                className="text-amber-600 hover:underline font-medium"
              >
                Regenerate your plan
              </button>{' '}
              to unlock purpose explanations and focus points.
            </p>
          </div>
        )}

        {/* Intervals */}
        {day.intervals && day.intervals.length > 0 && (
          <div className="mt-4">
            <h3 className="font-semibold text-sm text-gray-800 mb-2 flex items-center gap-1.5">
              <BarChart2 size={15} />
              Interval Breakdown
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-1.5 text-gray-500 font-medium">#</th>
                    <th className="text-left py-1.5 text-gray-500 font-medium">Duration</th>
                    <th className="text-left py-1.5 text-gray-500 font-medium">Power</th>
                    <th className="text-left py-1.5 text-gray-500 font-medium">Rest</th>
                  </tr>
                </thead>
                <tbody>
                  {day.intervals.map((iv, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-1.5 text-gray-400">{i + 1}</td>
                      <td className="py-1.5 text-gray-900 font-medium">
                        {iv.duration >= 60
                          ? `${Math.floor(iv.duration / 60)}:${String(iv.duration % 60).padStart(2, '0')}`
                          : `${iv.duration}s`}
                      </td>
                      <td className="py-1.5 text-gray-900 font-medium">{iv.power}W</td>
                      <td className="py-1.5 text-gray-500">
                        {iv.rest >= 60
                          ? `${Math.floor(iv.rest / 60)}:${String(iv.rest % 60).padStart(2, '0')}`
                          : `${iv.rest}s`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Feedback summary */}
        {day.completed && day.feedback && (
          <div className="mt-5 bg-green-50 rounded-xl p-4">
            <h3 className="font-semibold text-sm text-green-800 mb-2">Your Workout Log</h3>
            <div className="grid grid-cols-2 gap-2 text-xs text-green-700">
              <div>Duration: {day.feedback.actualDurationMinutes} min</div>
              {day.feedback.averagePower && <div>Avg Power: {day.feedback.averagePower}W</div>}
              {day.feedback.averageHeartRate && <div>Avg HR: {day.feedback.averageHeartRate} bpm</div>}
              {day.feedback.peakPower && <div>Peak Power: {day.feedback.peakPower}W</div>}
              <div>Effort: {day.feedback.perceivedEffort}/5 ({effortLabels[day.feedback.perceivedEffort]})</div>
            </div>
            {day.feedback.notes && (
              <p className="text-xs text-green-700 mt-2 border-t border-green-200 pt-2">{day.feedback.notes}</p>
            )}
            {/* Coach rating */}
            {rateWorkoutMutation.isPending && (
              <div className="mt-3 border-t border-green-200 pt-3 flex items-center gap-2 text-xs text-green-600">
                <Loader2 size={13} className="animate-spin" />
                Coach is reviewing your session…
              </div>
            )}
            {!rateWorkoutMutation.isPending && day.coachFeedback && (
              <div className="mt-3 border-t border-green-200 pt-3">
                <p className="text-xs font-semibold text-green-800 mb-1 flex items-center gap-1">
                  <Bot size={12} /> Coach's Feedback
                </p>
                <p className="text-xs text-green-700 leading-relaxed">{day.coachFeedback}</p>
                {rateWorkoutMutation.data?.followUpQuestion && (
                  <div className="mt-2 bg-amber-50 border border-amber-200 rounded-lg p-3">
                    <p className="text-xs font-semibold text-amber-800 mb-1">Coach wants to know:</p>
                    <p className="text-xs text-amber-700 leading-relaxed italic">
                      {rateWorkoutMutation.data.followUpQuestion}
                    </p>
                    {rateWorkoutMutation.data.suggestedFeedbackTags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {rateWorkoutMutation.data.suggestedFeedbackTags.map((tag) => (
                          <span
                            key={tag}
                            className="inline-block text-xs bg-amber-100 text-amber-700 rounded-full px-2 py-0.5 capitalize"
                          >
                            {tag.replace(/_/g, ' ')}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {!day.completed && day.workoutType !== 'rest' && (
          <button
            onClick={() => setShowForm(true)}
            className="mt-5 w-full bg-amber-500 text-white rounded-xl py-3 font-semibold hover:bg-amber-600 transition-colors"
          >
            Log Completed Workout
          </button>
        )}
      </div>

      <AIChat contextWorkout={day} />

      {showForm && (
        <WorkoutFeedbackForm
          day={day}
          onSubmit={handleFeedback}
          onCancel={() => setShowForm(false)}
        />
      )}
    </div>
  )
}
