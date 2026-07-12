import type {
  AiProvider,
  AthleteMetricSnapshot,
  ChatMessage,
  RiderAssessment,
  RaceEvent,
  RideMetricPoint,
  StravaConnection,
  IntervalsConnection,
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
  stravaAutoSyncEnabled?: boolean
  intervalsAnalysisComplete?: boolean
  lastIntervalsActivityId?: number | null
  intervalsAutoSyncEnabled?: boolean
  bikeType?: UserProfile['bikeType']
  trainingGoal?: string
  raceDate?: string | null
  raceDescription?: string | null
  weeklyHours?: number
  followsTrainingPlan: boolean
  maxHeartRate?: number
  restingHeartRate?: number
  currentFTP?: number
  consumedTokens: number
  fitnessLevel?: UserProfile['fitnessLevel']
  aiProvider: AiProvider
  riderAssessment?: RiderAssessment | null
  stravaConnection?: StravaConnection | null
  intervalsConnection?: IntervalsConnection | null
}

export interface LoadedUserData {
  profile: UserProfile
  isOnboarded: boolean
  stravaAnalysisComplete: boolean
  lastStravaActivityId: number | null
  stravaAutoSyncEnabled: boolean
  intervalsAnalysisComplete: boolean
  lastIntervalsActivityId: number | null
  intervalsAutoSyncEnabled: boolean
  aiProvider: AiProvider
  riderAssessment: RiderAssessment | null
  stravaConnection: StravaConnection | null
  intervalsConnection: IntervalsConnection | null
}

function normalizeTrainingGoal(goal?: string): UserProfile['trainingGoal'] {
  return goal === 'race' ? 'race' : 'general_fitness'
}

type UserProfileUpdates = Omit<Partial<UserProfile>, 'raceDate' | 'raceDescription'> & {
  raceDate?: string | null
  raceDescription?: string | null
  isOnboarded?: boolean
  stravaAnalysisComplete?: boolean
  lastStravaActivityId?: number | null
  stravaAutoSyncEnabled?: boolean
  intervalsAnalysisComplete?: boolean
  lastIntervalsActivityId?: number | null
  intervalsAutoSyncEnabled?: boolean
  aiProvider?: AiProvider
}

export async function fetchCurrentUser(token: string): Promise<LoadedUserData> {
  const user = await apiFetch<BackendUserResponse>('/users/me', { token })
  return {
    profile: {
      name: user.name ?? '',
      email: user.email,
      bikeType: user.bikeType ?? 'road',
      trainingGoal: normalizeTrainingGoal(user.trainingGoal),
      raceDate: user.raceDate ?? undefined,
      raceDescription: user.raceDescription ?? undefined,
      weeklyHours: user.weeklyHours ?? 8,
      followsTrainingPlan: user.followsTrainingPlan ?? false,
      maxHeartRate: user.maxHeartRate,
      restingHeartRate: user.restingHeartRate,
      currentFTP: user.currentFTP,
      consumedTokens: user.consumedTokens ?? 0,
      fitnessLevel: user.fitnessLevel ?? 'intermediate',
    },
    isOnboarded: user.isOnboarded,
    stravaAnalysisComplete: user.stravaAnalysisComplete,
    lastStravaActivityId: user.lastStravaActivityId ?? null,
    stravaAutoSyncEnabled: user.stravaAutoSyncEnabled ?? true,
    intervalsAnalysisComplete: user.intervalsAnalysisComplete ?? false,
    lastIntervalsActivityId: user.lastIntervalsActivityId ?? null,
    intervalsAutoSyncEnabled: user.intervalsAutoSyncEnabled ?? true,
    aiProvider: user.aiProvider ?? 'openai',
    riderAssessment: user.riderAssessment ?? null,
    stravaConnection: user.stravaConnection ?? null,
    intervalsConnection: user.intervalsConnection ?? null,
  }
}

export async function updateCurrentUser(
  token: string,
  updates: UserProfileUpdates
): Promise<LoadedUserData> {
  const body = {
    name: updates.name,
    bikeType: updates.bikeType,
    trainingGoal: updates.trainingGoal,
    raceDate: 'raceDate' in updates ? updates.raceDate ?? null : undefined,
    raceDescription: 'raceDescription' in updates ? updates.raceDescription ?? null : undefined,
    weeklyHours: updates.weeklyHours,
    followsTrainingPlan: updates.followsTrainingPlan,
    maxHeartRate: updates.maxHeartRate,
    restingHeartRate: updates.restingHeartRate,
    currentFTP: updates.currentFTP,
    fitnessLevel: updates.fitnessLevel,
    isOnboarded: updates.isOnboarded,
    stravaAnalysisComplete: updates.stravaAnalysisComplete,
    lastStravaActivityId: updates.lastStravaActivityId,
    stravaAutoSyncEnabled: updates.stravaAutoSyncEnabled,
    intervalsAnalysisComplete: updates.intervalsAnalysisComplete,
    lastIntervalsActivityId: updates.lastIntervalsActivityId,
    intervalsAutoSyncEnabled: updates.intervalsAutoSyncEnabled,
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

/**
 * One athlete-visible plan-day change (issue #357). `source` is the raw backend
 * trigger key (`coach_chat`, `ride_review`, …) — map it via utils/planHistory.
 * `applied === false` marks an automated change a user pin / completed day blocked.
 */
export interface PlanDayHistoryEntry {
  id: string
  date: string
  source: string
  applied: boolean
  recordedAt: string
  oldDay: Partial<TrainingDay> | null
  newDay: Partial<TrainingDay> | null
}

export interface PlanDayHistoryStats {
  bySource: Record<string, number>
  appliedCount: number
  blockedCount: number
  mostChangedDates: Array<{ date: string; count: number }>
  total: number
}

export async function fetchPlanHistory(
  token: string,
  date?: string,
): Promise<PlanDayHistoryEntry[]> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  const response = await apiFetch<{ entries: PlanDayHistoryEntry[]; total: number }>(
    `/users/me/plan-history${query}`,
    { token },
  )
  return response.entries
}

export async function fetchPlanHistoryStats(token: string): Promise<PlanDayHistoryStats> {
  return apiFetch<PlanDayHistoryStats>('/users/me/plan-history/stats', { token })
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

export type AthleteMemoryFactStatus =
  | 'active'
  | 'stale'
  | 'archived'
  | 'rejected'
  | 'user_confirmed'
  | 'needs_validation'

// A stable fact (FTP, max HR, weight) vs an observation of repeated behaviour (#386).
export type AthleteMemoryFactKind = 'fact' | 'observation'

export interface AthleteMemoryFact {
  id: string
  fact: string
  kind: AthleteMemoryFactKind
  category: string
  sourceSnippet: string
  sourceExchangeId: string | null
  firstObservedAt: string
  lastConfirmedAt: string
  confidence: number
  status: AthleteMemoryFactStatus
  contradictionNote: string | null
  observationCount: number
  updatedAt: string
}

export async function fetchAthleteMemoryFacts(token: string): Promise<AthleteMemoryFact[]> {
  const response = await apiFetch<{ facts: AthleteMemoryFact[] }>(
    '/users/me/athlete-memory-facts',
    { token }
  )
  return response.facts
}

export async function observeAthleteMemoryFact(
  token: string,
  fact: {
    fact: string
    kind?: AthleteMemoryFactKind
    category?: string
    sourceSnippet?: string
    confidence?: number
  }
): Promise<AthleteMemoryFact> {
  return apiFetch<AthleteMemoryFact>('/users/me/athlete-memory-facts', {
    token,
    method: 'POST',
    body: fact,
  })
}

export async function updateAthleteMemoryFact(
  token: string,
  factId: string,
  changes: {
    fact?: string
    kind?: AthleteMemoryFactKind
    category?: string
    status?: AthleteMemoryFactStatus
  }
): Promise<AthleteMemoryFact> {
  return apiFetch<AthleteMemoryFact>(`/users/me/athlete-memory-facts/${factId}`, {
    token,
    method: 'PATCH',
    body: changes,
  })
}

export async function confirmAthleteMemoryFact(
  token: string,
  factId: string
): Promise<AthleteMemoryFact> {
  return updateAthleteMemoryFact(token, factId, { status: 'user_confirmed' })
}

export async function deleteAthleteMemoryFact(token: string, factId: string): Promise<void> {
  await apiFetch(`/users/me/athlete-memory-facts/${factId}`, {
    token,
    method: 'DELETE',
  })
}

// #384: long-term structured athlete model (durable physiology & performance).
export interface AthleteModel {
  ftpWatts: number | null
  vo2max: number | null
  pacingQuality: string
  recoveryAbility: string
  thresholdDurability: string
  heatTolerance: string
  preferredTrainingStyle: string
  strengths: string[]
  weaknesses: string[]
  riskFactors: string[]
  summary: string
  confidence: number
  updatedAt: string | null
}

// Athlete-editable subset (excludes coach/server-owned confidence & updatedAt).
export type AthleteModelEdit = Omit<AthleteModel, 'confidence' | 'updatedAt'>

export async function fetchAthleteModel(token: string): Promise<AthleteModel> {
  return apiFetch<AthleteModel>('/users/me/athlete-model', { token })
}

export async function saveAthleteModel(
  token: string,
  model: AthleteModelEdit
): Promise<AthleteModel> {
  return apiFetch<AthleteModel>('/users/me/athlete-model', {
    token,
    method: 'PUT',
    body: model,
  })
}

export type AthleteHypothesisStatus = 'proposed' | 'confirmed' | 'refuted'

export interface AthleteHypothesis {
  id: string
  statement: string
  category: string
  rationale: string
  confidence: number
  evidenceCount: number
  status: AthleteHypothesisStatus
  firstProposedAt: string
  updatedAt: string
}

export async function fetchAthleteHypotheses(token: string): Promise<AthleteHypothesis[]> {
  const response = await apiFetch<{ hypotheses: AthleteHypothesis[] }>(
    '/users/me/athlete-hypotheses',
    { token }
  )
  return response.hypotheses
}

export async function updateAthleteHypothesis(
  token: string,
  hypothesisId: string,
  changes: {
    statement?: string
    category?: string
    rationale?: string
    status?: AthleteHypothesisStatus
  }
): Promise<AthleteHypothesis> {
  return apiFetch<AthleteHypothesis>(`/users/me/athlete-hypotheses/${hypothesisId}`, {
    token,
    method: 'PATCH',
    body: changes,
  })
}

export async function confirmAthleteHypothesis(
  token: string,
  hypothesisId: string
): Promise<AthleteHypothesis> {
  return updateAthleteHypothesis(token, hypothesisId, { status: 'confirmed' })
}

export async function refuteAthleteHypothesis(
  token: string,
  hypothesisId: string
): Promise<AthleteHypothesis> {
  return updateAthleteHypothesis(token, hypothesisId, { status: 'refuted' })
}

export async function deleteAthleteHypothesis(
  token: string,
  hypothesisId: string
): Promise<void> {
  await apiFetch(`/users/me/athlete-hypotheses/${hypothesisId}`, {
    token,
    method: 'DELETE',
  })
}

export type AthleteOpenQuestionStatus = 'open' | 'answered' | 'dismissed'

export interface AthleteOpenQuestion {
  id: string
  question: string
  category: string
  evidence: string
  needs: string
  evidenceCount: number
  status: AthleteOpenQuestionStatus
  resolution: string | null
  firstAskedAt: string
  updatedAt: string
}

export async function fetchAthleteOpenQuestions(
  token: string
): Promise<AthleteOpenQuestion[]> {
  const response = await apiFetch<{ openQuestions: AthleteOpenQuestion[] }>(
    '/users/me/open-questions',
    { token }
  )
  return response.openQuestions
}

export async function updateAthleteOpenQuestion(
  token: string,
  questionId: string,
  changes: {
    question?: string
    category?: string
    evidence?: string
    needs?: string
    resolution?: string
    status?: AthleteOpenQuestionStatus
  }
): Promise<AthleteOpenQuestion> {
  return apiFetch<AthleteOpenQuestion>(`/users/me/open-questions/${questionId}`, {
    token,
    method: 'PATCH',
    body: changes,
  })
}

export async function answerAthleteOpenQuestion(
  token: string,
  questionId: string
): Promise<AthleteOpenQuestion> {
  return updateAthleteOpenQuestion(token, questionId, { status: 'answered' })
}

export async function dismissAthleteOpenQuestion(
  token: string,
  questionId: string
): Promise<AthleteOpenQuestion> {
  return updateAthleteOpenQuestion(token, questionId, { status: 'dismissed' })
}

export async function deleteAthleteOpenQuestion(
  token: string,
  questionId: string
): Promise<void> {
  await apiFetch(`/users/me/open-questions/${questionId}`, {
    token,
    method: 'DELETE',
  })
}

export type AthleteExperimentStatus = 'suggested' | 'completed' | 'dismissed'

export interface AthleteExperiment {
  id: string
  hypothesisId: string | null
  question: string
  protocol: string
  rationale: string
  category: string
  status: AthleteExperimentStatus
  createdAt: string
  updatedAt: string
}

export async function fetchValidationExperiments(
  token: string
): Promise<AthleteExperiment[]> {
  const response = await apiFetch<{ experiments: AthleteExperiment[] }>(
    '/users/me/validation-experiments',
    { token }
  )
  return response.experiments
}

export async function updateValidationExperiment(
  token: string,
  experimentId: string,
  changes: {
    question?: string
    protocol?: string
    rationale?: string
    category?: string
    status?: AthleteExperimentStatus
  }
): Promise<AthleteExperiment> {
  return apiFetch<AthleteExperiment>(
    `/users/me/validation-experiments/${experimentId}`,
    {
      token,
      method: 'PATCH',
      body: changes,
    }
  )
}

export async function completeValidationExperiment(
  token: string,
  experimentId: string
): Promise<AthleteExperiment> {
  return updateValidationExperiment(token, experimentId, { status: 'completed' })
}

export async function dismissValidationExperiment(
  token: string,
  experimentId: string
): Promise<AthleteExperiment> {
  return updateValidationExperiment(token, experimentId, { status: 'dismissed' })
}

export async function deleteValidationExperiment(
  token: string,
  experimentId: string
): Promise<void> {
  await apiFetch(`/users/me/validation-experiments/${experimentId}`, {
    token,
    method: 'DELETE',
  })
}

export type AthletePredictionStatus = 'pending' | 'correct' | 'incorrect'

export interface AthletePrediction {
  id: string
  prediction: string
  expectedOutcome: string
  actualOutcome: string | null
  horizon: string
  category: string
  confidence: number
  status: AthletePredictionStatus
  createdAt: string
  evaluatedAt: string | null
  updatedAt: string
}

export interface AthletePredictionAccuracy {
  evaluated: number
  correct: number
  accuracy: number | null
}

export interface AthletePredictionsResult {
  predictions: AthletePrediction[]
  accuracy: AthletePredictionAccuracy
}

export async function fetchAthletePredictions(
  token: string
): Promise<AthletePredictionsResult> {
  return apiFetch<AthletePredictionsResult>('/users/me/predictions', { token })
}

export async function updateAthletePrediction(
  token: string,
  predictionId: string,
  changes: {
    prediction?: string
    expectedOutcome?: string
    actualOutcome?: string
    horizon?: string
    category?: string
    status?: AthletePredictionStatus
  }
): Promise<AthletePrediction> {
  return apiFetch<AthletePrediction>(`/users/me/predictions/${predictionId}`, {
    token,
    method: 'PATCH',
    body: changes,
  })
}

export async function markPredictionCorrect(
  token: string,
  predictionId: string
): Promise<AthletePrediction> {
  return updateAthletePrediction(token, predictionId, { status: 'correct' })
}

export async function markPredictionIncorrect(
  token: string,
  predictionId: string
): Promise<AthletePrediction> {
  return updateAthletePrediction(token, predictionId, { status: 'incorrect' })
}

export async function deleteAthletePrediction(
  token: string,
  predictionId: string
): Promise<void> {
  await apiFetch(`/users/me/predictions/${predictionId}`, {
    token,
    method: 'DELETE',
  })
}

export interface MemoryPrivacySettings {
  memoryUpdatesEnabled: boolean
}

export async function fetchMemoryPrivacySettings(token: string): Promise<MemoryPrivacySettings> {
  return apiFetch<MemoryPrivacySettings>('/users/me/memory-privacy', { token })
}

export async function updateMemoryPrivacySettings(
  token: string,
  settings: MemoryPrivacySettings
): Promise<MemoryPrivacySettings> {
  return apiFetch<MemoryPrivacySettings>('/users/me/memory-privacy', {
    token,
    method: 'PUT',
    body: settings,
  })
}

export async function clearAllMemory(token: string): Promise<void> {
  await apiFetch('/users/me/memory', { token, method: 'DELETE' })
}

export async function exportMemory(token: string): Promise<unknown> {
  return apiFetch<unknown>('/users/me/memory-export', { token })
}

export interface AIKeyStatus {
  provider: string
  hasOpenaiKey: boolean
  hasGeminiKey: boolean
}

export async function fetchAIKeyStatus(token: string): Promise<AIKeyStatus> {
  return apiFetch<AIKeyStatus>('/users/me/ai-key/status', { token })
}

export async function saveAIKey(
  token: string,
  provider: string,
  apiKey: string,
): Promise<AIKeyStatus> {
  return apiFetch<AIKeyStatus>('/users/me/ai-key', {
    token,
    method: 'PUT',
    body: { provider, apiKey },
  })
}

export async function deleteAIKey(token: string, provider: string): Promise<void> {
  await apiFetch(`/users/me/ai-key?provider=${encodeURIComponent(provider)}`, {
    token,
    method: 'DELETE',
  })
}

export async function testAIKey(
  token: string,
  provider: string,
  apiKey: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/users/me/ai-key/test', {
    token,
    method: 'POST',
    body: { provider, apiKey },
  })
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

/**
 * Set (or clear, with `legs: null`) the athlete's quick "how the legs felt"
 * rating for a ride. Everything richer is captured conversationally with the
 * coach, so this is the only structured field set directly from the dashboard.
 */
export async function setRideLegs(
  token: string,
  stravaActivityId: number,
  legs: 'fresh' | 'normal' | 'heavy' | null,
): Promise<{ stravaActivityId: number; ride?: RideMetricPoint | null }> {
  return apiFetch<{ stravaActivityId: number; ride?: RideMetricPoint | null }>(
    `/users/me/ride-feedback/${stravaActivityId}`,
    { token, method: 'PATCH', body: { legs } },
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
): Promise<FitUploadSummary> {
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
  return normalizeFitUploadSummary(await response.json())
}

export interface FitUploadSummary {
  status: string
  activityId: string
  sportType: string
  durationMinutes: number
  averagePower?: number
  averageHeartRate?: number
}

export interface FitUploadFileResult {
  filename: string
  status: 'imported' | 'skipped' | 'failed'
  message: string
  activityId?: string
  sportType?: string
  durationMinutes?: number
  averagePower?: number
  averageHeartRate?: number
}

export interface FitBulkUploadResponse {
  status: string
  total: number
  imported: number
  skipped: number
  failed: number
  files: FitUploadFileResult[]
}

interface RawFitUploadSummary {
  status: string
  activityId?: string
  activity_id?: string
  sportType?: string
  sport_type?: string
  durationMinutes?: number
  duration_minutes?: number
  averagePower?: number
  average_power?: number
  averageHeartRate?: number
  average_heart_rate?: number
}

function normalizeFitUploadSummary(raw: RawFitUploadSummary): FitUploadSummary {
  return {
    status: raw.status,
    activityId: raw.activityId ?? raw.activity_id ?? '',
    sportType: raw.sportType ?? raw.sport_type ?? '',
    durationMinutes: raw.durationMinutes ?? raw.duration_minutes ?? 0,
    averagePower: raw.averagePower ?? raw.average_power,
    averageHeartRate: raw.averageHeartRate ?? raw.average_heart_rate,
  }
}

export async function uploadFitFiles(
  token: string,
  files: File[],
): Promise<FitBulkUploadResponse> {
  const { API_BASE } = await import('./api')
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  const response = await fetch(`${API_BASE}/users/me/upload-fit/bulk`, {
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
