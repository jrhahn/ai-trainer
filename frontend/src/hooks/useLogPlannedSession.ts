import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore, workoutLogKey } from '../store/useAppStore'
import type { StravaActivity, TrainingDay, WorkoutFeedback } from '../store/useAppStore'
import { rateCompletedWorkout, type WorkoutRatingResult } from '../services/ai'
import {
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  fetchTrainingPlan,
  saveTrainingPlan,
  saveWorkoutLog,
} from '../services/user'
import { sessionKey, sessionSlot } from '../utils/planSessions'

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
 * 2. the server, awaited (ai-trainer-ops#127). A failed save undoes the tick and
 *    throws, so the form can say so and offer the save again: the tick used to
 *    stay, nothing was stored, and after a reload the session was open again.
 *    A saved log then refreshes the activity lists and the load numbers, which
 *    stayed old until a reload;
 * 3. the coach's rating, written onto the logged session's own slot only, so a
 *    two-a-day keeps the other session's note (#496). Only for a saved log.
 */
export function useLogPlannedSession() {
  const queryClient = useQueryClient()
  const {
    authToken,
    logWorkout,
    userProfile,
    updateTrainingDay,
    setTrainingPlan,
    setRideMetricsHistory,
    setMetricsHistory,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      logWorkout: s.logWorkout,
      userProfile: s.userProfile,
      updateTrainingDay: s.updateTrainingDay,
      setTrainingPlan: s.setTrainingPlan,
      setRideMetricsHistory: s.setRideMetricsHistory,
      setMetricsHistory: s.setMetricsHistory,
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
      // The server marks a logged session completed when the log arrives
      // (ai-trainer-ops#111), and a plan save no longer clears that. A log that
      // has not reached the server yet is still only known here, though, and
      // taken as-is the server's plan would wipe it — the card offered "Log it"
      // again and the coach's note and follow-up question vanished until a
      // reload. So completion and feedback also come from what this client knows.
      const known = new Map(
        useAppStore.getState().trainingPlan.map((d) => [sessionKey(d), d])
      )
      const withFeedback = latestPlan.map((d) => {
        const local = known.get(sessionKey(d))
        const kept = local?.completed
          ? { ...d, completed: true, feedback: local.feedback ?? d.feedback }
          : d
        return d.date === dayWithFeedback.date && sessionSlot(d) === slot
          ? { ...kept, completed: true, feedback: dayWithFeedback.feedback, coachFeedback: rating.feedback }
          : kept
      })
      setTrainingPlan(withFeedback)
      await saveTrainingPlan(authToken!, withFeedback)
    },
  })

  /** Log ``day``. Rejects with the server's error when the save fails, after
   *  putting the session back the way it was. */
  const logSession = async (day: TrainingDay, feedback: WorkoutFeedback) => {
    const slot = sessionSlot(day)
    const before = useAppStore.getState()
    const previous = before.trainingPlan.find((d) => d.date === day.date && sessionSlot(d) === slot)
    const logKey = workoutLogKey(day.date, slot)
    const hadLog = logKey in before.workoutLogs
    const previousLog = before.workoutLogs[logKey]
    logWorkout(day.date, feedback, slot)

    if (authToken) {
      try {
        await saveWorkoutLog(authToken, day.date, feedback, slot)
      } catch (error) {
        updateTrainingDay(day.date, { completed: previous?.completed, feedback: previous?.feedback }, slot)
        useAppStore.setState((state) => {
          const workoutLogs = { ...state.workoutLogs }
          if (hadLog) workoutLogs[logKey] = previousLog
          else delete workoutLogs[logKey]
          return { workoutLogs }
        })
        console.error('[LogSession] the log was not saved', { date: day.date, slot, error })
        throw error
      }
      // The log is a session in the load chain now, as an added activity is.
      // Not awaited: the log is stored, so the form need not wait for the lists.
      // An async function, so even a failure thrown before the first await
      // cannot reach the log that already succeeded.
      const token = authToken
      void (async () => {
        try {
          const [rides, metrics] = await Promise.all([
            fetchRideMetricsHistory(token),
            fetchMetricsHistory(token),
          ])
          setRideMetricsHistory(rides)
          setMetricsHistory(metrics)
        } catch (error) {
          // Saved; the lists catch up on the next load.
          console.warn('[LogSession] saved, but the lists could not be refreshed', error)
        }
      })()
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
