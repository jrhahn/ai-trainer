import { useMemo } from 'react'
import { skipToken, useQuery } from '@tanstack/react-query'
import { queryClient } from '../lib/queryClient'
import type { TrainingDay, WorkoutFeedback } from '../types/domain'
import { sessionSlot, sessionsForDate } from '../utils/planSessions'
import { PLAN_QUERY_KEY, WORKOUT_LOGS_QUERY_KEY } from './keys'

/*
 * The training plan and the workout logs: the first server state out of the
 * store and into react-query (ai-trainer-ops#273). Every write goes through
 * this module, and a session's completion comes from its log when the plan is
 * read. Before, each writer had to copy it into the plan: setting the plan was
 * a plain replace, and one writer merged the logs back in by hand.
 *
 * Both live in the app's one query client. The store's loadUserData fills them
 * with the rest of the account, and sign-out clears them with the rest of the
 * cache (`endSession`). They do not fetch by themselves, and they are not
 * collected while nobody reads them.
 */

const PLAN = [PLAN_QUERY_KEY] as const
const LOGS = [WORKOUT_LOGS_QUERY_KEY] as const
queryClient.setQueryDefaults(PLAN, { staleTime: Infinity, gcTime: Infinity })
queryClient.setQueryDefaults(LOGS, { staleTime: Infinity, gcTime: Infinity })

type WorkoutLogs = Record<string, WorkoutFeedback>
const NO_PLAN: TrainingDay[] = []
const NO_LOGS: WorkoutLogs = {}

/**
 * The key a session's workout log is stored under (#496).
 *
 * The first session of a date keeps the bare date, so every log written before
 * two-a-days existed still resolves; only the extra sessions add a `#slot`
 * suffix. This mirrors the backend's `GET /users/me/workouts` keying exactly.
 */
export function workoutLogKey(date: string, slot?: number): string {
  const resolved = sessionSlot({ date, slot })
  return resolved === 0 ? date : `${date}#${resolved}`
}

/** The slot an update targets: the given one, or the day's first session. */
function targetSlot(plan: TrainingDay[], date: string, slot: number | undefined): number {
  if (slot !== undefined) return slot
  const sessions = sessionsForDate(plan, date)
  return sessions.length > 0 ? sessionSlot(sessions[0]) : 0
}

/** A logged session is completed, and carries its log as feedback. */
function withLogs(plan: TrainingDay[], logs: WorkoutLogs): TrainingDay[] {
  return plan.map((day) => {
    const feedback = logs[workoutLogKey(day.date, day.slot)]
    return feedback ? { ...day, completed: true, feedback } : day
  })
}

const storedPlan = () => queryClient.getQueryData<TrainingDay[]>(PLAN) ?? NO_PLAN
const storedLogs = () => queryClient.getQueryData<WorkoutLogs>(LOGS) ?? NO_LOGS

/** The plan as the app shows it, outside React: for an event handler or a hook's callback. */
export function getPlan(): TrainingDay[] {
  return withLogs(storedPlan(), storedLogs())
}

export function getWorkoutLogs(): WorkoutLogs {
  return storedLogs()
}

/** The plan as the app shows it, for a component: it renders again when the plan or a log changes. */
export function usePlan(): TrainingDay[] {
  const { data: plan } = useQuery<TrainingDay[]>({ queryKey: PLAN, queryFn: skipToken }, queryClient)
  const { data: logs } = useQuery<WorkoutLogs>({ queryKey: LOGS, queryFn: skipToken }, queryClient)
  return useMemo(() => withLogs(plan ?? NO_PLAN, logs ?? NO_LOGS), [plan, logs])
}

/** The plan the server stored or sent, in place of the one held. */
export function setPlan(plan: TrainingDay[]): void {
  queryClient.setQueryData(PLAN, plan)
}

/** The plan and the logs, as loadUserData loads them. */
export function setPlanAndLogs(plan: TrainingDay[], logs: WorkoutLogs): void {
  queryClient.setQueryData(PLAN, plan)
  queryClient.setQueryData(LOGS, logs)
}

/** Log feedback for one session; `slot` defaults to the day's first (#496). */
export function logWorkout(date: string, feedback: WorkoutFeedback, slot?: number): void {
  const target = targetSlot(storedPlan(), date, slot)
  queryClient.setQueryData(LOGS, { ...storedLogs(), [workoutLogKey(date, target)]: feedback })
}

/** Put a session's log back the way it was before `logWorkout`: a save that failed. */
export function restoreWorkoutLog(key: string, previous: WorkoutFeedback | undefined): void {
  const logs = { ...storedLogs() }
  if (previous) logs[key] = previous
  else delete logs[key]
  queryClient.setQueryData(LOGS, logs)
}

/**
 * Patch one planned session. `slot` picks which session on `date` when the day
 * holds a two-a-day (#496); omit it to patch the day's first session, which is
 * the only one a single-session day has.
 */
export function updatePlanDay(date: string, updates: Partial<TrainingDay>, slot?: number): void {
  const plan = storedPlan()
  const target = targetSlot(plan, date, slot)
  setPlan(plan.map((day) => (day.date === date && sessionSlot(day) === target ? { ...day, ...updates } : day)))
}
