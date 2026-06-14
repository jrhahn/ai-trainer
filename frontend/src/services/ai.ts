import type {
  AiProvider,
  RaceEvent,
  RiderAssessment,
  RideMetricPoint,
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

export interface RideLabelUpdate {
  stravaActivityId: number
  labelOverride: string
}

export interface AskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  rideLabelUpdates?: RideLabelUpdate[]
}

interface BackendAskTrainerResult {
  response: string
  planUpdates?: PlanDayUpdate[]
  plan_updates?: PlanDayUpdate[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  rideLabelUpdates?: RideLabelUpdate[]
  ride_label_updates?: RideLabelUpdate[]
}

export const MAX_CONVERSATION_HISTORY = 20

export async function analyseStravaActivities(
  activities: StravaActivity[],
  authToken: string,
  maxHeartRate?: number,
  currentFTP?: number,
  source?: 'strava' | 'intervals'
): Promise<AnalyseActivitiesResult> {
  const raw = await apiFetch<BackendAnalyseActivitiesResult>('/ai/analyse-activities', {
    token: authToken,
    method: 'POST',
    body: {
      activities,
      ...(maxHeartRate !== undefined ? { maxHeartRate } : {}),
      ...(currentFTP !== undefined ? { currentFTP } : {}),
      ...(source !== undefined ? { source } : {}),
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
    rideLabelUpdates: result.rideLabelUpdates ?? result.ride_label_updates,
  }
}

export async function fetchRaceEventFeedback(
  event: RaceEvent,
  authToken: string,
  action: 'added' | 'updated' = 'added',
): Promise<string> {
  const result = await apiFetch<{ feedback: string }>('/ai/race-event-feedback', {
    token: authToken,
    method: 'POST',
    body: { event, action },
  })
  return result.feedback
}

export interface WorkoutRatingResult {
  feedback: string
  needsAthleteFeedback: boolean
  followUpQuestion: string | null
  suggestedFeedbackTags: string[]
}

export async function rateCompletedWorkout(
  day: TrainingDay,
  authToken: string,
  stravaActivityId?: number
): Promise<WorkoutRatingResult> {
  const result = await apiFetch<{
    feedback: string
    needs_athlete_feedback: boolean
    follow_up_question: string | null
    suggested_feedback_tags: string[]
  }>('/ai/rate-workout', {
    token: authToken,
    method: 'POST',
    body: {
      day,
      ...(stravaActivityId !== undefined ? { stravaActivityId } : {}),
    },
  })
  return {
    feedback: result.feedback,
    needsAthleteFeedback: result.needs_athlete_feedback ?? false,
    followUpQuestion: result.follow_up_question ?? null,
    suggestedFeedbackTags: result.suggested_feedback_tags ?? [],
  }
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

export interface NextRideRecommendationResult {
  response: string
  nextSessionRecommendation: string
  recommendationType: 'keep_as_planned' | 'easier' | 'recovery' | 'move_intensity'
  planUpdates?: PlanDayUpdate[]
}

interface BackendNextRideRecommendationResult {
  response: string
  next_session_recommendation: string
  recommendation_type: string
  planUpdates?: PlanDayUpdate[]
}

export async function fetchNextRideRecommendation(
  authToken: string,
  stravaActivityId?: number,
): Promise<NextRideRecommendationResult> {
  const raw = await apiFetch<BackendNextRideRecommendationResult>('/ai/next-ride-recommendation', {
    token: authToken,
    method: 'POST',
    body: {
      ...(stravaActivityId !== undefined ? { stravaActivityId } : {}),
    },
  })
  return {
    response: raw.response,
    nextSessionRecommendation: raw.next_session_recommendation,
    recommendationType: (raw.recommendation_type ?? 'keep_as_planned') as NextRideRecommendationResult['recommendationType'],
    planUpdates: raw.planUpdates,
  }
}

export async function resolveRideMatch(
  authToken: string,
  plannedDate: string,
  stravaActivityId: number,
): Promise<{ ride: RideMetricPoint; coachNote?: string | null; planUpdates?: PlanDayUpdate[] }> {
  return apiFetch<{ ride: RideMetricPoint; coachNote?: string | null; planUpdates?: PlanDayUpdate[] }>(
    '/ai/resolve-ride-match',
    {
      token: authToken,
      method: 'POST',
      body: { plannedDate, stravaActivityId },
    },
  )
}

export async function processPendingFeedbacks(
  authToken: string,
  activityIds: Array<number | string>,
): Promise<string> {
  const result = await apiFetch<{ loginSummary: string }>('/ai/process-pending-feedbacks', {
    token: authToken,
    method: 'POST',
    body: { activityIds },
  })
  return result.loginSummary ?? ''
}
