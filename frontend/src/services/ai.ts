import type {
  AiProvider,
  ChatMessage,
  DayPreference,
  RaceEvent,
  RiderAssessment,
  RideMetricPoint,
  Sport,
  StravaActivity,
  TrainingDay,
  UserProfile,
  Weekday,
} from '../store/useAppStore'
import type { AthleteModel, AthletePerformanceModel } from './user'
import { apiFetch } from './api'

export type { AiProvider }

export interface AskTrainerOptions {
  contextWorkout?: TrainingDay
  /** The question is a question-card answer the chat already holds, so the
   * backend does not store it a second time (ai-trainer-ops#103 Q28). */
  alreadyInChat?: boolean
}

export interface PlanDayUpdate {
  date: string
  workoutType?: TrainingDay['workoutType']
  title?: string
  description?: string
  durationMinutes?: number
  durationMinMinutes?: number
  durationMaxMinutes?: number
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

// What the chat captured or proposes, confirmed under the reply (ai-trainer-ops#65, #71).
export interface CapturedGoal {
  id: string
  label: string
}

// An illness or a tired day the chat stored (ai-trainer-ops#18). "Well again"
// ends an illness and has nothing to take back, so it is not removable.
export interface CapturedState {
  id: string
  label: string
  removable: boolean
}

// Hours, training days or sports said in chat, offered as a profile change
// (ai-trainer-ops#43). Only the fields that differ from the profile are set.
export interface ProfileProposal {
  weeklyHours?: number | null
  /** An FTP the athlete stated, offered rather than set (ai-trainer-ops#129). */
  ftp?: number | null
  trainingDays?: Weekday[] | null
  sports?: Sport[] | null
  /** Said in chat about oneself ("I'm a beginner"), offered (ai-trainer-ops#202). */
  fitnessLevel?: UserProfile['fitnessLevel'] | null
  maxHeartRate?: number | null
  /** The training days swap in a day the athlete generally prefers: a lasting
   *  change, which the offer says (ai-trainer-ops#204). */
  standingDaySwap?: boolean
}

export interface RaceProposal {
  raceDate?: string | null
  raceDescription?: string | null
  // Upcoming calendar races that clearing removes too.
  calendarRaces?: number
}

/** The race a moved race replaces: the profile's, or one in the calendar. */
export interface RaceMovedFrom {
  date: string
  kind: 'profile' | 'calendar'
  eventId?: string | null
}

/** A race the athlete said is coming; the server read the date (ai-trainer-ops#175). */
export interface RaceOffer {
  date: string
  /** Set when the athlete said the race moves (#175, comment 5111). */
  replaces?: RaceMovedFrom | null
}

export type CoachSawItem = NonNullable<ChatMessage['coachSaw']>[number]

export interface AskTrainerResult {
  response: string
  coachSaw?: CoachSawItem[]
  capturedGoals?: CapturedGoal[]
  capturedStates?: CapturedState[]
  // Same shape as a state: a day off or a long ride the chat pinned (ai-trainer-ops#110).
  capturedConstraints?: CapturedState[]
  /** A weekday preference the chat kept (ai-trainer-ops#204). */
  capturedDayPreferences?: DayPreference[]
  raceProposal?: RaceProposal | null
  raceOffer?: RaceOffer | null
  profileProposal?: ProfileProposal | null
  planUpdates?: PlanDayUpdate[]
  updatedPlan?: TrainingDay[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  rideLabelUpdates?: RideLabelUpdate[]
  physiologyRationale?: string
  contextRationale?: string
  objectiveRationale?: string
}

interface BackendAskTrainerResult {
  response: string
  coachSaw?: CoachSawItem[]
  capturedGoals?: CapturedGoal[]
  capturedStates?: CapturedState[]
  capturedConstraints?: CapturedState[]
  capturedDayPreferences?: DayPreference[]
  raceProposal?: RaceProposal | null
  raceOffer?: RaceOffer | null
  profileProposal?: ProfileProposal | null
  planUpdates?: PlanDayUpdate[]
  plan_updates?: PlanDayUpdate[]
  updatedPlan?: TrainingDay[]
  updated_plan?: TrainingDay[]
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  rideLabelUpdates?: RideLabelUpdate[]
  ride_label_updates?: RideLabelUpdate[]
  physiologyRationale?: string
  physiology_rationale?: string
  contextRationale?: string
  context_rationale?: string
  objectiveRationale?: string
  objective_rationale?: string
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

/** What moved since the plan was generated (ai-trainer-ops#158). */
export interface PlanInputsChanged {
  changed: boolean
  reasons: string[]
  /** Full rebuilds still allowed today (ai-trainer-ops#175). */
  rebuildsLeft: number
}

export async function fetchPlanInputsChanged(authToken: string): Promise<PlanInputsChanged> {
  return apiFetch<PlanInputsChanged>('/ai/plan-inputs-changed', { token: authToken })
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
      alreadyInChat: options.alreadyInChat,
    },
  })

  return {
    response: result.response,
    planUpdates: result.planUpdates ?? result.plan_updates,
    updatedPlan: result.updatedPlan ?? result.updated_plan,
    sources: result.sources,
    rideLabelUpdates: result.rideLabelUpdates ?? result.ride_label_updates,
    physiologyRationale: result.physiologyRationale ?? result.physiology_rationale,
    contextRationale: result.contextRationale ?? result.context_rationale,
    objectiveRationale: result.objectiveRationale ?? result.objective_rationale,
    capturedGoals: result.capturedGoals ?? [],
    capturedStates: result.capturedStates ?? [],
    capturedConstraints: result.capturedConstraints ?? [],
    capturedDayPreferences: result.capturedDayPreferences ?? [],
    coachSaw: result.coachSaw ?? [],
    raceProposal: result.raceProposal ?? null,
    raceOffer: result.raceOffer ?? null,
    profileProposal: result.profileProposal ?? null,
  }
}

export interface AthleteFactCandidate {
  fact: string
  category: string
  confidence: number
  sourceSnippet: string
}

interface BackendAthleteFactCandidate {
  fact: string
  category?: string
  confidence?: number
  sourceSnippet?: string
  source_snippet?: string
}

export async function extractAthleteFacts(
  transcript: string,
  authToken: string
): Promise<AthleteFactCandidate[]> {
  const result = await apiFetch<{ candidates: BackendAthleteFactCandidate[] }>(
    '/ai/extract-athlete-facts',
    {
      token: authToken,
      method: 'POST',
      body: { transcript },
    }
  )
  return (result.candidates ?? []).map((c) => ({
    fact: c.fact,
    category: c.category ?? 'general',
    confidence: c.confidence ?? 0.35,
    sourceSnippet: c.sourceSnippet ?? c.source_snippet ?? '',
  }))
}

// #384: re-derive the long-term athlete model from training history on demand.
export async function refreshAthleteModel(authToken: string): Promise<AthleteModel> {
  return apiFetch<AthleteModel>('/ai/refresh-athlete-model', {
    token: authToken,
    method: 'POST',
  })
}

// #475/#477/#478: read the deterministic, evidence-backed performance model.
export async function fetchAthletePerformanceModel(
  authToken: string
): Promise<AthletePerformanceModel> {
  return apiFetch<AthletePerformanceModel>('/ai/athlete-performance-model', {
    token: authToken,
  })
}

// Re-derive the deterministic performance model from training history on demand.
export async function refreshAthletePerformanceModel(
  authToken: string
): Promise<AthletePerformanceModel> {
  return apiFetch<AthletePerformanceModel>('/ai/refresh-athlete-performance-model', {
    token: authToken,
    method: 'POST',
  })
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

/** One week of the season plan (ai-trainer-ops#98). The numbers are computed;
 * only `goal` is written by the model, and it is null until it has been. */
export interface SeasonWeek {
  weekStart: string
  phase: 'base' | 'build' | 'peak' | 'taper' | 'transition' | 'maintenance'
  focus: string
  recovery: boolean
  loadWeek: number | null
  hoursMin: number | null
  hoursMax: number | null
  hardSessionsMin: number
  hardSessionsMax: number
  keySession: string
  nextRace: string | null
  goal: string | null
}

export async function fetchSeasonPlan(authToken: string): Promise<SeasonWeek[]> {
  return (await apiFetch<{ weeks: SeasonWeek[] }>('/ai/season-plan', { token: authToken })).weeks
}

/** Writes the goals the weeks are missing; one model call on the server. */
export async function writeWeekGoals(authToken: string): Promise<SeasonWeek[]> {
  return (
    await apiFetch<{ weeks: SeasonWeek[] }>('/ai/season-plan/goals', { token: authToken, method: 'POST' })
  ).weeks
}

/** The plan's texts in the interface language (ai-trainer-ops#121). */
export interface PlanTranslations {
  language: string | null
  translations: Record<string, string>
  missing: boolean
}

/** The translations already written. No model call; `missing` says whether any are owed. */
export async function fetchPlanTranslations(authToken: string, language: string): Promise<PlanTranslations> {
  return apiFetch<PlanTranslations>('/ai/plan-translations', {
    token: authToken,
    headers: { 'X-App-Language': language },
  })
}

/** Writes the missing translations; one model call on the server. */
export async function writePlanTranslations(authToken: string, language: string): Promise<PlanTranslations> {
  return apiFetch<PlanTranslations>('/ai/plan-translations', {
    token: authToken,
    method: 'POST',
    headers: { 'X-App-Language': language },
  })
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

export type ReasoningSource =
  | 'personal_observation'
  | 'scientific_evidence'
  | 'coach_inference'

export interface ReasoningItem {
  source: ReasoningSource
  text: string
}

export interface ReadinessRecommendation {
  recommendation: string
  reasoning: ReasoningItem[]
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
  recommendations: ReadinessRecommendation[]
}

interface BackendReasoningItem {
  source?: string
  text?: string
}

interface BackendReadinessRecommendation {
  recommendation: string
  reasoning?: BackendReasoningItem[]
}

const REASONING_SOURCES: readonly ReasoningSource[] = [
  'personal_observation',
  'scientific_evidence',
  'coach_inference',
]

function normalizeReasoning(items?: BackendReasoningItem[]): ReasoningItem[] {
  return (items ?? []).map((item) => {
    const source = REASONING_SOURCES.includes(item.source as ReasoningSource)
      ? (item.source as ReasoningSource)
      : 'coach_inference'
    return { source, text: item.text ?? '' }
  })
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
  recommendations?: BackendReadinessRecommendation[]
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
    recommendations: (raw.recommendations ?? []).map((rec) => ({
      recommendation: rec.recommendation,
      reasoning: normalizeReasoning(rec.reasoning),
    })),
  }
}

export async function refreshLoginSummary(authToken: string): Promise<string> {
  const raw = await apiFetch<{ loginSummary: string }>('/ai/refresh-login-summary', {
    token: authToken,
    method: 'POST',
  })
  return raw.loginSummary ?? ''
}

export interface TrainingStatusBadge {
  label: string
  tone: 'positive' | 'steady' | 'caution'
  rationale: string
}

/** Regenerate the coach-authored dashboard status badge (#499).
 *
 * The badge is deliberately not computed in the browser: the backend owns it so
 * the coach holds the same label the athlete is reading and can explain it.
 */
export async function refreshTrainingStatus(
  authToken: string
): Promise<TrainingStatusBadge | null> {
  const raw = await apiFetch<{
    label?: string
    tone?: string
    rationale?: string
  }>('/ai/refresh-training-status', { token: authToken, method: 'POST' })
  if (!raw.label) return null
  const tone = raw.tone === 'positive' || raw.tone === 'caution' ? raw.tone : 'steady'
  return { label: raw.label, tone, rationale: raw.rationale ?? '' }
}

export async function resolveRideMatch(
  authToken: string,
  plannedDate: string,
  stravaActivityId: number,
  // Which session of that date, when it holds more than one. Omitted means the
  // only session there is, which is what a single-session day sends (#547).
  plannedSlot?: number,
  // The provider's own string id. A non-Strava ride's synthesized 63-bit
  // stravaActivityId is float64-corrupted the moment it becomes a JS number
  // (7846148097020609552 -> ...609536), so on its own it matches no row and the
  // resolve 404s with "Could not save that" (#441).
  externalActivityId?: string | null,
): Promise<{ ride: RideMetricPoint; coachNote?: string | null; planUpdates?: PlanDayUpdate[] }> {
  return apiFetch<{ ride: RideMetricPoint; coachNote?: string | null; planUpdates?: PlanDayUpdate[] }>(
    '/ai/resolve-ride-match',
    {
      token: authToken,
      method: 'POST',
      body: {
        plannedDate,
        stravaActivityId,
        externalActivityId: externalActivityId ?? null,
        ...(plannedSlot === undefined ? {} : { plannedSlot }),
      },
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
