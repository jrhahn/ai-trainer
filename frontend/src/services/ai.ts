import type {
  RiderAssessment,
  StravaActivity,
  TrainingDay,
  UserProfile,
  WorkoutFeedback,
} from '../store/useAppStore'
import { apiFetch } from './api'

export type AiProvider = 'openai' | 'gemini'

type ConversationMessage = { role: 'user' | 'assistant'; content: string }

export interface AskTrainerOptions {
  coachMemory?: string
  conversationHistory?: ConversationMessage[]
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
}

export interface AskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
}

interface BackendAskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
  plan_updates?: PlanDayUpdate[]
}

export const MAX_CONVERSATION_HISTORY = 20

export async function analyseStravaActivities(
  activities: StravaActivity[],
  authToken: string,
  maxHeartRate?: number
): Promise<RiderAssessment> {
  return apiFetch<RiderAssessment>('/ai/analyse-activities', {
    token: authToken,
    method: 'POST',
    body: {
      activities,
      ...(maxHeartRate !== undefined ? { maxHeartRate } : {}),
    },
  })
}

export async function generateTrainingPlan(
  profile: UserProfile,
  authToken: string,
  riderAssessment?: RiderAssessment
): Promise<TrainingDay[]> {
  return apiFetch<TrainingDay[]>('/ai/generate-plan', {
    token: authToken,
    method: 'POST',
    body: { profile, riderAssessment },
  })
}

export async function adaptTrainingPlan(
  plan: TrainingDay[],
  recentFeedback: WorkoutFeedback[],
  profile: UserProfile,
  authToken: string
): Promise<TrainingDay[]> {
  return apiFetch<TrainingDay[]>('/ai/adapt-plan', {
    token: authToken,
    method: 'POST',
    body: { plan, recentFeedback, profile },
  })
}

export async function askTrainer(
  question: string,
  plan: TrainingDay[],
  profile: UserProfile,
  authToken: string,
  options: AskTrainerOptions = {}
): Promise<AskTrainerResult> {
  const result = await apiFetch<BackendAskTrainerResult>('/ai/ask-trainer', {
    token: authToken,
    method: 'POST',
    body: {
      question,
      plan,
      profile,
      coachMemory: options.coachMemory,
      conversationHistory: options.conversationHistory,
      contextWorkout: options.contextWorkout,
    },
  })

  return {
    response: result.response,
    planUpdates: result.planUpdates ?? result.plan_updates,
  }
}

export async function updateCoachMemory(
  currentMemory: string,
  userMessage: string,
  coachResponse: string,
  authToken: string
): Promise<string> {
  const result = await apiFetch<{ memory: string }>('/ai/update-coach-memory', {
    token: authToken,
    method: 'POST',
    body: { currentMemory, userMessage, coachResponse },
  })
  return result.memory
}

export async function rateCompletedWorkout(
  day: TrainingDay,
  profile: UserProfile,
  authToken: string
): Promise<string> {
  const result = await apiFetch<{ feedback: string }>('/ai/rate-workout', {
    token: authToken,
    method: 'POST',
    body: { day, profile },
  })
  return result.feedback
}
