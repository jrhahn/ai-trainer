import type {
  AiProvider,
  AthleteHomeLocation,
  AthleteMetricSnapshot,
  ChatMessage,
  DailyForecast,
  RiderAssessment,
  RaceEvent,
  RideMetricPoint,
  StravaConnection,
  IntervalsConnection,
  TrainingDay,
  UserProfile,
  WorkoutFeedback,
} from '../types/domain'
import { apiFetch } from './api'
import type { UserResponse } from '../types/api.gen'

// What `/users/me` answers, from the backend's schema (ai-trainer-ops#276):
// hand-written, it had drifted to non-null for fields the backend sends as null.
type BackendUserResponse = UserResponse

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
  /** Advisory message when the stored FTP is implausible against the
   * athlete's recorded maximal aerobic power.  Null when the pair looks sane
   * or no MAP reference has been recorded yet. */
  ftpPlausibilityWarning: string | null
  riderAssessment: RiderAssessment | null
  stravaConnection: StravaConnection | null
  intervalsConnection: IntervalsConnection | null
  stravaAvailable: boolean
}

function normalizeTrainingGoal(goal?: string): UserProfile['trainingGoal'] {
  return goal === 'race' ? 'race' : 'general_fitness'
}

type UserProfileUpdates = Omit<Partial<UserProfile>, 'raceDate' | 'raceDescription' | 'weightKg'> & {
  raceDate?: string | null
  raceDescription?: string | null
  /** Null removes it (ai-trainer-ops#188). */
  weightKg?: number | null
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
  // The response model types the choices as plain strings (`bikeType`, the
  // sports and days, the level, the provider, the rider type); the app reads
  // them as its own unions, as it did before the types were generated.
  return {
    profile: {
      name: user.name ?? '',
      email: user.email,
      bikeType: (user.bikeType ?? 'road') as UserProfile['bikeType'],
      trainingGoal: normalizeTrainingGoal(user.trainingGoal ?? undefined),
      raceDate: user.raceDate ?? undefined,
      raceDescription: user.raceDescription ?? undefined,
      weeklyHours: user.weeklyHours ?? undefined,
      sports: (user.sports ?? undefined) as UserProfile['sports'],
      trainingDays: (user.trainingDays ?? undefined) as UserProfile['trainingDays'],
      followsTrainingPlan: user.followsTrainingPlan ?? false,
      // The backend sends null for an unknown value; the profile has none.
      maxHeartRate: user.maxHeartRate ?? undefined,
      restingHeartRate: user.restingHeartRate ?? undefined,
      currentFTP: user.currentFTP ?? undefined,
      thresholdPaceSecondsPerKm: user.thresholdPaceSecondsPerKm ?? undefined,
      weightKg: user.weightKg ?? undefined,
      dayPreferences: user.dayPreferences ?? [],
      consumedTokens: user.consumedTokens ?? 0,
      fitnessLevel: (user.fitnessLevel ?? null) as UserProfile['fitnessLevel'],
    },
    isOnboarded: user.isOnboarded,
    stravaAnalysisComplete: user.stravaAnalysisComplete,
    lastStravaActivityId: user.lastStravaActivityId ?? null,
    stravaAutoSyncEnabled: user.stravaAutoSyncEnabled ?? true,
    intervalsAnalysisComplete: user.intervalsAnalysisComplete ?? false,
    lastIntervalsActivityId: user.lastIntervalsActivityId ?? null,
    intervalsAutoSyncEnabled: user.intervalsAutoSyncEnabled ?? true,
    aiProvider: (user.aiProvider ?? 'gemini') as AiProvider,
    ftpPlausibilityWarning: user.ftpPlausibilityWarning ?? null,
    riderAssessment: (user.riderAssessment ?? null) as RiderAssessment | null,
    stravaConnection: user.stravaConnection ?? null,
    intervalsConnection: user.intervalsConnection ?? null,
    // An older server does not say; it offered the button before, so keep it.
    stravaAvailable: user.stravaAvailable ?? true,
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
    sports: updates.sports,
    trainingDays: updates.trainingDays,
    followsTrainingPlan: updates.followsTrainingPlan,
    maxHeartRate: updates.maxHeartRate,
    restingHeartRate: updates.restingHeartRate,
    currentFTP: updates.currentFTP,
    thresholdPaceSecondsPerKm: updates.thresholdPaceSecondsPerKm,
    weightKg: 'weightKg' in updates ? updates.weightKg ?? null : undefined,
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

/**
 * Delete the account for good.
 *
 * The password is required by the backend since ai-trainer-ops#35: a live
 * session alone used to be enough to destroy an athlete's whole history, while
 * the neighbouring step-up routes (disabling the second factor, signing out
 * everywhere) both asked for more.
 */
export async function deleteCurrentUser(token: string, password: string): Promise<void> {
  await apiFetch('/users/me', { token, method: 'DELETE', body: { password } })
}

/**
 * Everything stored about the account, as one JSON document (ai-trainer-ops#6,
 * GDPR Art. 15 and 20). Credentials are left out by the backend.
 */
export async function exportAccountData(token: string): Promise<unknown> {
  return apiFetch<unknown>('/users/me/export', { token })
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
  // Shared by all rows from one coach run so the Coach Timeline can collapse a
  // run into a single card; null for rows written before the batch_id column.
  batchId: string | null
  oldDay: Partial<TrainingDay> | null
  newDay: Partial<TrainingDay> | null
  // One-line coach rationale for this day's change; null when not narrated.
  reason?: string | null
  // True when this run was narrated as a coach chat message — the Coach Timeline
  // suppresses the redundant plan-update card for narrated runs.
  narrated?: boolean
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

/**
 * Persist feedback for one planned session.
 *
 * `slot` names which session on `date` (#496); omitted or 0 is the day's first
 * session, which is what every single-session day has.
 */
export async function saveWorkoutLog(
  token: string,
  date: string,
  feedback: WorkoutFeedback,
  slot?: number
): Promise<void> {
  await apiFetch(`/users/me/workouts/${date}`, {
    token,
    method: 'POST',
    body: slot ? { feedback, slot } : { feedback },
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

export async function removeGoalRemote(token: string, goalId: string): Promise<void> {
  await apiFetch(`/users/me/goals/${goalId}`, { token, method: 'DELETE' })
}

export async function removeStateRemote(token: string, stateId: string): Promise<void> {
  await apiFetch(`/users/me/states/${stateId}`, { token, method: 'DELETE' })
}

// A day off or a long ride the chat pinned (ai-trainer-ops#110): every plan write
// keeps to it, so it can be taken back here as well as by saying so in the chat.
export async function removeConstraintRemote(token: string, constraintId: string): Promise<void> {
  await apiFetch(`/users/me/availability-constraints/${constraintId}`, { token, method: 'DELETE' })
}

/** Forget a day preference the chat kept (ai-trainer-ops#204). */
export async function removeDayPreferenceRemote(token: string, prefer: string, over: string): Promise<void> {
  await apiFetch(`/users/me/day-preferences/${encodeURIComponent(prefer)}/${encodeURIComponent(over)}`, {
    token,
    method: 'DELETE',
  })
}

export async function clearProfileRaceRemote(token: string): Promise<void> {
  await apiFetch('/users/me/race/clear', { token, method: 'POST' })
}

export async function fetchChatHistory(token: string): Promise<ChatMessage[]> {
  const response = await apiFetch<{ messages: ChatMessage[] }>('/users/me/chat', { token })
  return response.messages
}

export async function clearChatHistoryRemote(token: string): Promise<void> {
  await apiFetch('/users/me/chat', { token, method: 'DELETE' })
}

export async function fetchCoachMemory(token: string): Promise<string> {
  const response = await apiFetch<{ memory: string }>('/users/me/coach-memory', { token })
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

// ---------------------------------------------------------------------------
// Athlete motivation model (#562) — what the athlete trains FOR
// ---------------------------------------------------------------------------

export type MotivationSource = 'inferred' | 'user_set'
export type MotivationEntryStatus = 'active' | 'contradicted' | 'retired'
export type MotivationComponent =
  | 'enjoyment'
  | 'adaptation'
  | 'consistency'
  | 'health'
  | 'race_performance'

export interface MotivationEntry {
  text: string
  confidence: number
  source: MotivationSource
  sourceSnippet: string
  status: MotivationEntryStatus
  contradictionNote: string | null
  firstObservedAt: string | null
  lastConfirmedAt: string | null
  observationCount?: number
}

export interface AthleteMotivationModel {
  primaryObjective: string
  primaryObjectiveSource: MotivationSource
  primaryObjectiveConfidence: number
  primaryObjectiveSnippet: string
  secondaryObjectives: MotivationEntry[]
  constraints: MotivationEntry[]
  // Spans exactly MotivationComponent and sums to 1 — guaranteed by the backend
  // persist gate, so the UI renders it rather than re-checking it.
  utilityWeights: Record<string, number>
  pinnedWeights: MotivationComponent[]
  modalityAffinity: Record<string, number>
  updatedAt: string | null
}

/** A partial edit: omitted fields are left alone, they are not blanked. */
export interface AthleteMotivationModelEdit {
  primaryObjective?: string
  secondaryObjectives?: Array<Partial<MotivationEntry> & { text: string }>
  constraints?: Array<Partial<MotivationEntry> & { text: string }>
  utilityWeights?: Record<string, number>
  pinnedWeights?: MotivationComponent[]
}

export async function fetchMotivationModel(
  token: string
): Promise<AthleteMotivationModel> {
  return apiFetch<AthleteMotivationModel>('/users/me/motivation-model', { token })
}

export async function updateMotivationModel(
  token: string,
  changes: AthleteMotivationModelEdit
): Promise<AthleteMotivationModel> {
  return apiFetch<AthleteMotivationModel>('/users/me/motivation-model', {
    token,
    method: 'PUT',
    body: changes,
  })
}

/** One rule that fired during a learning run, as recorded in the trail (#566). */
export interface MotivationWeightRule {
  rule: string
  /** What the athlete did, phrased for the athlete. */
  signal: string
  observed: number
  strength: number
  effects: Record<string, number>
}

/**
 * One recorded change to the weight balance (#566).
 *
 * `rules` and `evidence` are null for the athlete's own edit — nothing was
 * inferred, so there is nothing to justify.
 */
export interface MotivationWeightEvent {
  id: string
  source: MotivationSource
  weightsBefore: Record<string, number>
  weightsAfter: Record<string, number>
  /** Only the components that moved, signed. */
  deltas: Record<string, number>
  rules: MotivationWeightRule[] | null
  evidence: Record<string, unknown> | null
  pinned: string[]
  recordedAt: string | null
}

export async function fetchMotivationWeightHistory(
  token: string
): Promise<MotivationWeightEvent[]> {
  return apiFetch<MotivationWeightEvent[]>(
    '/users/me/motivation-model/weight-history',
    { token }
  )
}


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
  // Structured supporting evidence and the competing explanations still to rule
  // out (#479). Empty for older LLM-formed hypotheses.
  evidence: string[]
  alternativeExplanations: string[]
  evidenceCount: number
  status: AthleteHypothesisStatus
  firstProposedAt: string
  updatedAt: string
}

// #475/#477/#478: deterministic, evidence-backed Athlete Performance Model.
export interface AthletePerformanceAttribute {
  estimate: number | null
  score: string | null
  confidence: number
  unit: string | null
  evidence: string[]
  missingInformation: string[]
  // #604: bounds and the test that would settle an estimate the data does not
  // pin down. Absent on qualitative or already-settled attributes.
  estimateLow?: number | null
  estimateHigh?: number | null
  validationProtocol?: string | null
}

export interface AthletePerformanceLimiter {
  limiter: string
  confidence: number
  evidence: string[]
  counterEvidence: string[]
}

export interface TrainingRoiSystemGain {
  system: string
  gain: string
  rationale: string
}

export interface TrainingRoiEmphasis {
  system: string
  label: string
  sessions: number
}

export interface TrainingRoiRecommendation {
  sufficient: boolean
  limiter: string | null
  confidence: number
  hypothesis: string
  rationale: string
  expectedGain: TrainingRoiSystemGain[]
  weeklyEmphasis: TrainingRoiEmphasis[]
}

export interface AthletePerformanceModel {
  attributes: Record<string, AthletePerformanceAttribute>
  likelyLimiter: string | null
  limiters: AthletePerformanceLimiter[]
  recommendations: TrainingRoiRecommendation | null
  sourceWindowDays: number | null
  derivedFromRides: number
  updatedAt: string | null
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

export type AthleteInquiryStatus =
  | 'pending'
  | 'answered'
  | 'needs_settings'
  | 'dismissed'

/** A question the coach put to the athlete because data cannot answer it (#506). */
export interface AthleteInquiry {
  id: string
  question: string
  category: string
  whyAsking: string
  settingsHint: string
  status: AthleteInquiryStatus
  answer: string | null
  askCount: number
  followUpNote: string | null
  askedAt: string
  answeredAt: string | null
  updatedAt: string
  /** Answers the athlete can tap instead of typing (ai-trainer-ops#155). */
  options?: string[] | null
}

export interface AthleteInquiryAnswerResult {
  inquiry: AthleteInquiry
  /** Whether the answer resolved the question. */
  accepted: boolean
  /** What the coach said back: the acknowledgement, rephrasing, or hand-off. */
  coachReply: string
  /** The answer also asks for something only the coach chat can do, such as a
   * plan change, so the chat sends it on (ai-trainer-ops#103 Q28). */
  forwardToCoach?: boolean
}

export async function fetchAthleteInquiries(
  token: string
): Promise<AthleteInquiry[]> {
  const response = await apiFetch<{ inquiries: AthleteInquiry[] }>(
    '/users/me/inquiries',
    { token }
  )
  return response.inquiries
}

export async function answerAthleteInquiry(
  token: string,
  inquiryId: string,
  answer: string,
  option?: number
): Promise<AthleteInquiryAnswerResult> {
  return apiFetch<AthleteInquiryAnswerResult>(
    `/users/me/inquiries/${inquiryId}/answer`,
    { token, method: 'POST', body: option === undefined ? { answer } : { answer, option } }
  )
}

export async function dismissAthleteInquiry(
  token: string,
  inquiryId: string
): Promise<AthleteInquiry> {
  return apiFetch<AthleteInquiry>(`/users/me/inquiries/${inquiryId}/dismiss`, {
    token,
    method: 'POST',
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

/** What the coach chat's timeline shows, in one request (ai-trainer-ops#277):
 *  the lists of the five fetchers above, with their defaults. */
export interface CoachTimeline {
  history: PlanDayHistoryEntry[]
  questions: AthleteOpenQuestion[]
  hypotheses: AthleteHypothesis[]
  experiments: AthleteExperiment[]
  inquiries: AthleteInquiry[]
}

export async function fetchCoachTimeline(token: string): Promise<CoachTimeline> {
  const response = await apiFetch<{
    planHistory: PlanDayHistoryEntry[]
    openQuestions: AthleteOpenQuestion[]
    hypotheses: AthleteHypothesis[]
    experiments: AthleteExperiment[]
    inquiries: AthleteInquiry[]
  }>('/users/me/coach-timeline', { token })
  return {
    history: response.planHistory,
    questions: response.openQuestions,
    hypotheses: response.hypotheses,
    experiments: response.experiments,
    inquiries: response.inquiries,
  }
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
  token: string,
  { includeResolved = false }: { includeResolved?: boolean } = {}
): Promise<AthletePredictionsResult> {
  const query = includeResolved ? '?includeResolved=true' : ''
  return apiFetch<AthletePredictionsResult>(`/users/me/predictions${query}`, { token })
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
  /**
   * The coach model each provider actually runs, served by the backend (#691).
   *
   * These used to be hard-coded display strings in two components that
   * disagreed with each other and with the backend. Optional here only so an
   * older backend does not break the page; the components fall back to the
   * provider name rather than inventing a version number.
   */
  openaiModel?: string
  geminiModel?: string
  /**
   * Whether this athlete must supply a key before any AI call can succeed
   * (ai-trainer-ops#41).
   *
   * The two `has*Key` flags describe the account; this describes whether the
   * account can be *used*, which also depends on the deployment — whether
   * admin-key fallback is on, and whether the owner set a global key. Without
   * it the client cannot tell "you have no key and you need one" from "you have
   * no key and the server has one for you", which is how onboarding used to
   * walk all the way to its last step and fail there.
   *
   * Optional, and absent means not required: an older backend does not know the
   * field, and asking a hosted athlete for a key they do not need is a worse
   * failure than the one this fixes.
   */
  keyRequired?: boolean
  /**
   * The free allowance on the owner's key, in tokens per `allowanceWindowDays`;
   * `null` when unlimited, i.e. with the athlete's own key (ai-trainer-ops#79).
   */
  allowanceTokens?: number | null
  allowanceUsed?: number
  allowanceWindowDays?: number
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

/** A session entered by hand, outside any plan day (ai-trainer-ops#47). */
export interface ManualActivityInput {
  date: string
  sport: 'cycling' | 'running' | 'strength'
  durationMinutes: number
  perceivedEffort: 1 | 2 | 3 | 4 | 5
  /** Optional, and only offered for the sports that cover ground
   *  (ai-trainer-ops#47). `null` clears a stored one; omitting the key leaves it
   *  alone, which is what the backend's `model_fields_set` check distinguishes. */
  distanceKm?: number | null
  /** The watch's average heart rate and, for a ride, the head unit's normalized
   *  power (ai-trainer-ops#169). Same rule as the distance: `null` clears. */
  averageHeartRate?: number | null
  normalizedPower?: number | null
  notes?: string
}

export interface ManualActivityCreated {
  date: string
  slot: number
  sport: string
}

export async function addManualActivity(
  token: string,
  activity: ManualActivityInput
): Promise<ManualActivityCreated> {
  return apiFetch<ManualActivityCreated>('/users/me/activities', {
    token,
    method: 'POST',
    body: activity,
  })
}

/** A correction to an entered activity; the day and slot are its identity. */
export async function updateManualActivity(
  token: string,
  date: string,
  slot: number,
  activity: Omit<ManualActivityInput, 'date'>
): Promise<ManualActivityCreated> {
  return apiFetch<ManualActivityCreated>(`/users/me/activities/${date}/${slot}`, {
    token,
    method: 'PUT',
    body: activity,
  })
}

export async function deleteManualActivity(
  token: string,
  date: string,
  slot: number
): Promise<void> {
  await apiFetch(`/users/me/activities/${date}/${slot}`, { token, method: 'DELETE' })
}

export async function fetchRideMetricsHistory(token: string): Promise<RideMetricPoint[]> {
  const response = await apiFetch<{ rides: RideMetricPoint[] }>(
    '/users/me/ride-metrics-history',
    { token }
  )
  return response.rides
}

export interface WeatherForecastResult {
  location: AthleteHomeLocation | null
  days: DailyForecast[]
}

/**
 * Upcoming daily outlook near the athlete's training location (#495), used for
 * the weather shown on planned days. Served from the backend's hourly per-location
 * cache, so calling this on every dashboard load costs no upstream requests.
 */
export async function fetchWeatherForecast(
  token: string,
): Promise<WeatherForecastResult> {
  const response = await apiFetch<{
    location?: AthleteHomeLocation | null
    days?: DailyForecast[]
  }>('/users/me/weather-forecast', { token })
  return { location: response.location ?? null, days: response.days ?? [] }
}

/** Read the persisted training location, if the athlete has one. */
export async function fetchHomeLocation(
  token: string,
): Promise<AthleteHomeLocation | null> {
  const response = await apiFetch<{ location?: AthleteHomeLocation | null }>(
    '/users/me/home-location',
    { token },
  )
  return response.location ?? null
}

/**
 * Set the athlete's training location explicitly. Stored as `user_set`, which
 * pins it against the backend's ride-start inference.
 */
export async function saveHomeLocation(
  token: string,
  location: { latitude: number; longitude: number; label?: string },
): Promise<AthleteHomeLocation | null> {
  const response = await apiFetch<{ location?: AthleteHomeLocation | null }>(
    '/users/me/home-location',
    {
      token,
      method: 'PUT',
      body: {
        latitude: location.latitude,
        longitude: location.longitude,
        label: location.label ?? '',
      },
    },
  )
  return response.location ?? null
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
  externalActivityId?: string | null,
): Promise<{ stravaActivityId: number; ride?: RideMetricPoint | null }> {
  // Non-Strava rides (e.g. intervals.icu) carry a synthesized 63-bit
  // stravaActivityId that loses precision as a JS float64, so the path param
  // cannot locate the row. Send the precision-safe external id so the backend
  // can key the lookup off it (#441).
  return apiFetch<{ stravaActivityId: number; ride?: RideMetricPoint | null }>(
    `/users/me/ride-feedback/${stravaActivityId}`,
    {
      token,
      method: 'PATCH',
      body: { legs, externalActivityId: externalActivityId ?? null },
    },
  )
}

/** What the athlete may answer when asked what an unclassified session was.
 *
 * Mirrors `ATHLETE_PURPOSE_CHOICES` in `services/ride_purpose_question.py`, the
 * way `setRideLegs` mirrors the leg-feel values. The backend schema is the
 * enforcement point — an unknown value comes back 422.
 */
export type RidePurposeAnswer =
  | 'recovery'
  | 'endurance'
  | 'tempo'
  | 'interval_sweetspot'
  | 'interval_threshold'
  | 'interval_vo2max'
  | 'interval_sprints'
  | 'mixed'

/**
 * Answer the coach's "what was this session?" question, or skip it (#580).
 *
 * The coach used to ask this in prose inside the login summary, where there was
 * no answer field and nowhere for the answer to go. `purpose: null` records a
 * skip: the question stops without anything being claimed about the session.
 */
export async function answerRidePurpose(
  token: string,
  stravaActivityId: number,
  purpose: RidePurposeAnswer | null,
  externalActivityId?: string | null,
): Promise<{ stravaActivityId: number; ride?: RideMetricPoint | null }> {
  // Same float64 identity problem as `setRideLegs` (#441).
  return apiFetch<{ stravaActivityId: number; ride?: RideMetricPoint | null }>(
    `/users/me/ride-purpose/${stravaActivityId}`,
    {
      token,
      method: 'PATCH',
      body: { purpose, externalActivityId: externalActivityId ?? null },
    },
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

export interface FitUploadFileResult {
  filename: string
  status: 'imported' | 'skipped' | 'failed'
  message: string
  activityId?: string
  /** The day it happened, so the result can link there (#48). */
  activityDate?: string
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

export async function uploadFitFiles(
  token: string,
  files: File[],
): Promise<FitBulkUploadResponse> {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  return apiFetch<FitBulkUploadResponse>('/users/me/upload-fit/bulk', { token, method: 'POST', body: formData })
}

// The day board the planner got, with every deduction (ai-trainer-ops#17).
export interface DayBoardOption {
  key: string
  label: string
  prescription: string
  modality: string
  base: number
  heatCost: number
  freshnessCost: number
  competesWith: string
  competesWithLabel: string
  score: number
}

export interface DayBoard {
  hot: boolean
  heatTolerance: string
  options: DayBoardOption[]
  source: { path: string; line: number; symbol: string; ref: string }
}

export async function fetchDayBoard(token: string): Promise<DayBoard> {
  return apiFetch<DayBoard>('/users/me/day-board', { token })
}

/** "This is wrong" on a coach reply or a planned day (ai-trainer-ops#94). */
export async function flagWrong(
  token: string,
  flag: { target: 'reply' | 'day'; ref: string; excerpt: string; note?: string }
): Promise<void> {
  await apiFetch('/users/me/wrong-flags', { method: 'POST', token, body: flag })
}

/** The athlete's consent to the weekly coach review (ai-trainer-ops#136). */
export interface CoachReviewConsent {
  available: boolean
  consented: boolean
  consentedAt: string | null
  version: string
}

export async function fetchCoachReviewConsent(token: string): Promise<CoachReviewConsent> {
  return apiFetch<CoachReviewConsent>('/users/me/coach-review-consent', { token })
}

export async function updateCoachReviewConsent(
  token: string,
  consented: boolean
): Promise<CoachReviewConsent> {
  return apiFetch<CoachReviewConsent>('/users/me/coach-review-consent', {
    token,
    method: 'PUT',
    body: { consented },
  })
}
