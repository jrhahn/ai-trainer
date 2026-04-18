import type {
  AiProvider,
  RiderAssessment,
  StravaActivity,
  TrainingDay,
  WorkoutFeedback,
} from '../store/useAppStore'
import { apiFetch } from './api'

export type { AiProvider }

export interface AskTrainerOptions {
  contextWorkout?: TrainingDay
}

export interface PlanDayUpdate {
  date: string
  workoutType?: TrainingDay['workoutType']
  title?: string
  description?: string
  durationMinutes?: number
  targetPower?: TrainingDay['targetPower']
  targetHeartRate?: TrainingDay['targetHeartRate']
  intervals?: TrainingDay['intervals']
  workoutPurpose?: string
  keyFocusPoints?: string[]
}

export interface AnalyseActivitiesResult {
  assessment: RiderAssessment
  planUpdates?: PlanDayUpdate[]
}

interface BackendAnalyseActivitiesResult {
  assessment: RiderAssessment
  planUpdates?: PlanDayUpdate[]
  plan_updates?: PlanDayUpdate[]
}

export interface AskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
}

interface BackendAskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
  plan_updates?: PlanDayUpdate[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
}

export const MAX_CONVERSATION_HISTORY = 20

export async function analyseStravaActivities(
  activities: StravaActivity[],
  authToken: string,
  maxHeartRate?: number
): Promise<AnalyseActivitiesResult> {
  const raw = await apiFetch<BackendAnalyseActivitiesResult>('/ai/analyse-activities', {
    token: authToken,
    method: 'POST',
    body: {
      activities,
      ...(maxHeartRate !== undefined ? { maxHeartRate } : {}),
    },
  })
  return {
    assessment: raw.assessment,
    planUpdates: raw.planUpdates ?? raw.plan_updates,
  }
}

export async function generateTrainingPlan(authToken: string): Promise<TrainingDay[]> {
  return apiFetch<TrainingDay[]>('/ai/generate-plan', {
    token: authToken,
    method: 'POST',
    body: {},
  })
}

export async function adaptTrainingPlan(
  recentFeedback: WorkoutFeedback[],
  authToken: string
): Promise<TrainingDay[]> {
  return apiFetch<TrainingDay[]>('/ai/adapt-plan', {
    token: authToken,
    method: 'POST',
    body: { recentFeedback },
  })
}

export async function askTrainer(
  question: string,
  authToken: string,
  options: AskTrainerOptions = {}
): Promise<AskTrainerResult> {
  const result = await apiFetch<BackendAskTrainerResult>('/ai/ask-trainer', {
    token: authToken,
    method: 'POST',
    body: {
      question,
      contextWorkout: options.contextWorkout,
    },
  })

  return {
    response: result.response,
    planUpdates: result.planUpdates ?? result.plan_updates,
    sources: result.sources,
  }
}

export async function rateCompletedWorkout(
  day: TrainingDay,
  authToken: string
): Promise<string> {
  const result = await apiFetch<{ feedback: string }>('/ai/rate-workout', {
    token: authToken,
    method: 'POST',
    body: { day },
  })
  return result.feedback
}
