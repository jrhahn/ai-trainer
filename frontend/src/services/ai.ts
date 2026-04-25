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
  authToken: string,
  stravaActivityId?: number
): Promise<string> {
  const result = await apiFetch<{ feedback: string }>('/ai/rate-workout', {
    token: authToken,
    method: 'POST',
    body: {
      day,
      ...(stravaActivityId !== undefined ? { stravaActivityId } : {}),
    },
  })
  return result.feedback
}

export interface ReadinessScore {
  score: number
  formScore: number
  fitnessScore: number
  ctl: number
  atl: number
  tsb: number
  daysUntilRace: number
  raceDate?: string | null
  projectedScore?: number | null
  projectedCtl?: number | null
  projectedAtl?: number | null
  projectedTsb?: number | null
  recommendations: string[]
}

interface BackendReadinessScore {
  score: number
  form_score: number
  fitness_score: number
  ctl: number
  atl: number
  tsb: number
  days_until_race: number
  race_date?: string | null
  projected_score?: number | null
  projected_ctl?: number | null
  projected_atl?: number | null
  projected_tsb?: number | null
  recommendations?: string[]
}

export async function fetchReadinessScore(authToken: string): Promise<ReadinessScore> {
  const raw = await apiFetch<BackendReadinessScore>('/ai/readiness-score', { token: authToken })
  return {
    score: raw.score,
    formScore: raw.form_score,
    fitnessScore: raw.fitness_score,
    ctl: raw.ctl,
    atl: raw.atl,
    tsb: raw.tsb,
    daysUntilRace: raw.days_until_race,
    raceDate: raw.race_date,
    projectedScore: raw.projected_score,
    projectedCtl: raw.projected_ctl,
    projectedAtl: raw.projected_atl,
    projectedTsb: raw.projected_tsb,
    recommendations: raw.recommendations ?? [],
  }
}

export async function refreshLoginSummary(authToken: string): Promise<string> {
  const raw = await apiFetch<{ loginSummary: string }>('/ai/refresh-login-summary', {
    token: authToken,
    method: 'POST',
  })
  return raw.loginSummary ?? ''
}
