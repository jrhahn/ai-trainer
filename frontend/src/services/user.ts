import type {
  AiProvider,
  AthleteMetricSnapshot,
  ChatMessage,
  RiderAssessment,
  RaceEvent,
  RideMetricPoint,
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
  maxHeartRate?: number
  restingHeartRate?: number
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
      maxHeartRate: user.maxHeartRate,
      restingHeartRate: user.restingHeartRate,
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
    maxHeartRate: updates.maxHeartRate,
    restingHeartRate: updates.restingHeartRate,
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

export type RaceEventInput = Pick<RaceEvent, 'date' | 'distanceKm' | 'elevationM'> & {
  startTime?: string | null
}

export async function fetchRaceEvents(token: string): Promise<RaceEvent[]> {
  const response = await apiFetch<{ events: RaceEvent[] }>('/users/me/race-events', { token })
  return response.events
}

export async function createRaceEvent(token: string, event: RaceEventInput): Promise<RaceEvent> {
  return apiFetch<RaceEvent>('/users/me/race-events', {
    token,
    method: 'POST',
    body: event,
  })
}

export async function updateRaceEventRemote(
  token: string,
  eventId: string,
  event: RaceEventInput,
): Promise<RaceEvent> {
  return apiFetch<RaceEvent>(`/users/me/race-events/${eventId}`, {
    token,
    method: 'PUT',
    body: event,
  })
}

export async function deleteRaceEventRemote(token: string, eventId: string): Promise<void> {
  await apiFetch(`/users/me/race-events/${eventId}`, {
    token,
    method: 'DELETE',
  })
}

export async function fetchChatHistory(token: string): Promise<ChatMessage[]> {
  const response = await apiFetch<{ messages: ChatMessage[] }>('/users/me/chat', { token })
  return response.messages
}

export async function saveChatMessage(
  token: string,
  message: Pick<ChatMessage, 'role' | 'content' | 'timestamp'>,
): Promise<ChatMessage> {
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

export async function fetchRideMetricsHistory(token: string): Promise<RideMetricPoint[]> {
  const response = await apiFetch<{ rides: RideMetricPoint[] }>(
    '/users/me/ride-metrics-history',
    { token }
  )
  return response.rides
}

export async function submitRideFeedback(
  token: string,
  stravaActivityId: number,
  feedback: {
    rpe: number
    legs: 'fresh' | 'normal' | 'heavy'
    intent: 'planned workout' | 'recovery' | 'commute' | 'free ride' | 'aborted'
    note?: string
  },
): Promise<{ stravaActivityId: number; userNote: string }> {
  return apiFetch<{ stravaActivityId: number; userNote: string }>(
    `/users/me/ride-feedback/${stravaActivityId}`,
    { token, method: 'PATCH', body: feedback },
  )
}

export async function recalculateMetrics(
  token: string,
  ftpOverride?: number,
): Promise<{ updated: number; ftpUsed: number }> {
  return apiFetch<{ updated: number; ftpUsed: number }>('/users/me/recalculate-metrics', {
    token,
    method: 'POST',
    body: { ftpOverride: ftpOverride ?? null },
  })
}

export async function estimateFTP(
  token: string,
  opts: { maxHeartRate?: number; restingHeartRate?: number },
): Promise<{ estimatedFTP: number | null; source: string }> {
  return apiFetch<{ estimatedFTP: number | null; source: string }>('/users/me/estimate-ftp', {
    token,
    method: 'POST',
    body: {
      maxHeartRate: opts.maxHeartRate ?? null,
      restingHeartRate: opts.restingHeartRate ?? null,
    },
  })
}

export async function uploadFitFile(
  token: string,
  file: File,
): Promise<{ status: string; activityId: string; sportType: string; durationMinutes: number; averagePower?: number; averageHeartRate?: number }> {
  const { API_BASE } = await import('./api')
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE}/users/me/upload-fit`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  })
  if (!response.ok) {
    let message = 'Upload failed'
    try {
      const data = await response.json() as { detail?: string }
      if (data.detail) message = data.detail
    } catch { /* ignore */ }
    throw new Error(message)
  }
  return response.json()
}
