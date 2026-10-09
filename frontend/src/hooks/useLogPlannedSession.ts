import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { StravaActivity, TrainingDay, WorkoutFeedback } from '../store/useAppStore'
import { rateCompletedWorkout, type WorkoutRatingResult } from '../services/ai'
import { fetchTrainingPlan, saveTrainingPlan, saveWorkoutLog } from '../services/user'
import { sessionSlot } from '../utils/planSessions'

function stravaActivityType(activity: StravaActivity): string {
  return (activity.sport_type || activity.type || '').toLowerCase()
}

export function plannedWorkoutMatchesActivity(day: TrainingDay, activity: StravaActivity): boolean {
  const type = stravaActivityType(activity)
  if (day.workoutType === 'strength') {
    return type.includes('weight') || type.includes('strength') || type.includes('workout')
  }
  return type === 'cycling' || type.includes('ride')
}

/**
 * Log a planned session: what the day view did, now callable from anywhere
 * (ai-trainer-ops#52).
 *
 * The dashboard's session card logs too now, and two copies of "save the log,
 * then ask the coach to rate it, then write the note onto *that* session" would
 * drift the first time one of them changed. Three steps, as before:
 *
 * 1. the local store, optimistically, so the card flips to done at once;
 * 2. the server, best-effort — the optimistic state stands if the network fails;
 * 3. the coach's rating, written onto the logged session's own slot only, so a
 *    two-a-day keeps the other session's note (#496).
 */
export function useLogPlannedSession() {
  const queryClient = useQueryClient()
  const { authToken, logWorkout, userProfile, updateTrainingDay, setTrainingPlan } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      logWorkout: s.logWorkout,
      userProfile: s.userProfile,
      updateTrainingDay: s.updateTrainingDay,
      setTrainingPlan: s.setTrainingPlan,
    }))
  )

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
    onSuccess: async (rating: WorkoutRatingResult, { dayWithFeedback }) => {
      if (!rating.feedback) return
      const slot = sessionSlot(dayWithFeedback)
      updateTrainingDay(dayWithFeedback.date, { coachFeedback: rating.feedback }, slot)
      const latestPlan = await fetchTrainingPlan(authToken!)
      const withFeedback = latestPlan.map((d) =>
        d.date === dayWithFeedback.date && sessionSlot(d) === slot
          ? { ...d, coachFeedback: rating.feedback }
          : d
      )
      setTrainingPlan(withFeedback)
      await saveTrainingPlan(authToken!, withFeedback)
    },
  })

  const logSession = async (day: TrainingDay, feedback: WorkoutFeedback) => {
    const slot = sessionSlot(day)
    logWorkout(day.date, feedback, slot)

    if (authToken) {
      try {
        await saveWorkoutLog(authToken, day.date, feedback, slot)
      } catch {
        // keep optimistic local state even if the network fails
      }
    }

    if (authToken && userProfile) {
      rateWorkoutMutation.mutate({ dayWithFeedback: { ...day, completed: true, feedback } })
    }
  }

  return {
    logSession,
    isRating: rateWorkoutMutation.isPending,
    rating: rateWorkoutMutation.data,
  }
}
