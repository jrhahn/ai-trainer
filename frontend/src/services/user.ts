import type {
  AiProvider,
  AthleteMetricSnapshot,
  ChatMessage,
  RiderAssessment,
  StravaConnection,
  TrainingDay,
  UserProfile,
  WorkoutFeedback,
} from '../store/useAppStore'
import { apiFetch } from './api'

interface BackendUserResponse {
  id: string
  email: string
  name?: string
  isOnboarded: boolean
  stravaAnalysisComplete: boolean
  lastStravaActivityId?: number | null
  bikeType?: UserProfile['bikeType']
  trainingGoal?: UserProfile['trainingGoal']
  raceDate?: string
  raceDescription?: string
  weeklyHours?: number
  followsTrainingPlan: boolean
  restingHeartRate?: number
  maxHeartRate?: number
  thresholdHeartRate?: number
  currentFTP?: number
  fitnessLevel?: UserProfile['fitnessLevel']
  aiProvider: AiProvider
  riderAssessment?: RiderAssessment | null
  stravaConnection?: StravaConnection | null
}

export interface LoadedUserData {
  profile: UserProfile
  isOnboarded: boolean
  stravaAnalysisComplete: boolean
  lastStravaActivityId: number | null
  aiProvider: AiProvider
  riderAssessment: RiderAssessment | null
  stravaConnection: StravaConnection | null
}

export async function fetchCurrentUser(token: string): Promise<LoadedUserData> {
  const user = await apiFetch<BackendUserResponse>('/users/me', { token })
  return {
    profile: {
      name: user.name ?? '',
      email: user.email,
      bikeType: user.bikeType ?? 'road',
      trainingGoal: user.trainingGoal ?? 'general_fitness',
      raceDate: user.raceDate,
      raceDescription: user.raceDescription,
      weeklyHours: user.weeklyHours ?? 8,
      followsTrainingPlan: user.followsTrainingPlan ?? false,
      restingHeartRate: user.restingHeartRate,
      maxHeartRate: user.maxHeartRate,
      thresholdHeartRate: user.thresholdHeartRate,
      currentFTP: user.currentFTP,
      fitnessLevel: user.fitnessLevel ?? 'intermediate',
    },
    isOnboarded: user.isOnboarded,
    stravaAnalysisComplete: user.stravaAnalysisComplete,
    lastStravaActivityId: user.lastStravaActivityId ?? null,
    aiProvider: user.aiProvider ?? 'openai',
    riderAssessment: user.riderAssessment ?? null,
    stravaConnection: user.stravaConnection ?? null,
  }
}

export async function updateCurrentUser(
  token: string,
  updates: Partial<UserProfile> & {
    isOnboarded?: boolean
    stravaAnalysisComplete?: boolean
    lastStravaActivityId?: number | null
    aiProvider?: AiProvider
  }
): Promise<LoadedUserData> {
  const body = {
    name: updates.name,
    bikeType: updates.bikeType,
    trainingGoal: updates.trainingGoal,
    raceDate: updates.raceDate,
    raceDescription: updates.raceDescription,
    weeklyHours: updates.weeklyHours,
    followsTrainingPlan: updates.followsTrainingPlan,
    restingHeartRate: updates.restingHeartRate,
    maxHeartRate: updates.maxHeartRate,
    thresholdHeartRate: updates.thresholdHeartRate,
    currentFTP: updates.currentFTP,
    fitnessLevel: updates.fitnessLevel,
    isOnboarded: updates.isOnboarded,
    stravaAnalysisComplete: updates.stravaAnalysisComplete,
    lastStravaActivityId: updates.lastStravaActivityId,
    aiProvider: updates.aiProvider,
  }
  await apiFetch('/users/me', { token, method: 'PUT', body })
  return fetchCurrentUser(token)
}

export async function deleteCurrentUser(token: string): Promise<void> {
  await apiFetch('/users/me', { token, method: 'DELETE' })
}

export async function fetchTrainingPlan(token: string): Promise<TrainingDay[]> {
  const response = await apiFetch<{ plan: TrainingDay[] }>('/users/me/plan', { token })
  return response.plan
}

export async function saveTrainingPlan(token: string, plan: TrainingDay[]): Promise<TrainingDay[]> {
  const response = await apiFetch<{ plan: TrainingDay[] }>('/users/me/plan', {
    token,
    method: 'PUT',
    body: { plan },
  })
  return response.plan
}

export async function fetchWorkoutLogs(token: string): Promise<Record<string, WorkoutFeedback>> {
  return apiFetch<Record<string, WorkoutFeedback>>('/users/me/workouts', { token })
}

export async function saveWorkoutLog(token: string, date: string, feedback: WorkoutFeedback): Promise<void> {
  await apiFetch(`/users/me/workouts/${date}`, {
    token,
    method: 'POST',
    body: { feedback },
  })
}

export async function fetchChatHistory(token: string): Promise<ChatMessage[]> {
  const response = await apiFetch<{ messages: ChatMessage[] }>('/users/me/chat', { token })
  return response.messages
}

export async function saveChatMessage(token: string, message: ChatMessage): Promise<ChatMessage> {
  return apiFetch<ChatMessage>('/users/me/chat', {
    token,
    method: 'POST',
    body: message,
  })
}

export async function clearChatHistoryRemote(token: string): Promise<void> {
  await apiFetch('/users/me/chat', { token, method: 'DELETE' })
}

export async function fetchCoachMemory(token: string): Promise<string> {
  const response = await apiFetch<{ memory: string }>('/users/me/coach-memory', { token })
  return response.memory
}

export async function saveCoachMemoryRemote(token: string, memory: string): Promise<string> {
  const response = await apiFetch<{ memory: string }>('/users/me/coach-memory', {
    token,
    method: 'PUT',
    body: { memory },
  })
  return response.memory
}

export async function fetchMetricsHistory(token: string): Promise<AthleteMetricSnapshot[]> {
  const response = await apiFetch<{ snapshots: AthleteMetricSnapshot[] }>(
    '/users/me/metrics-history',
    { token }
  )
  return response.snapshots
}
