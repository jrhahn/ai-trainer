import type {
  AiProvider,
  ChatMessage,
  DayPreference,
  PlanRebuild,
  RiderAssessment,
  RideMetricPoint,
  Sport,
  StravaActivity,
  TrainingDay,
  UserProfile,
  Weekday,
} from '../types/domain'
import type { AthleteModel, AthletePerformanceModel } from './user'
import { apiFetch } from './api'

export type { AiProvider }

export interface AskTrainerOptions {
  contextWorkout?: TrainingDay
  /** The question is a question-card answer the chat already holds, so the
   * backend does not store it a second time (ai-trainer-ops#103 Q28). */
  alreadyInChat?: boolean
  /** Stops waiting for the reply: the athlete's "Stop waiting" (ai-trainer-ops#267). */
  signal?: AbortSignal
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
export type ReplyNote = NonNullable<ChatMessage['notes']>[number]
export type DaySummary = NonNullable<ChatMessage['daySummaries']>[number]

export interface AskTrainerResult {
  response: string
  notes?: ReplyNote[]
  /** The coach's sentence per day the reply changed (ai-trainer-ops#266). */
  daySummaries?: DaySummary[]
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
  notes?: ReplyNote[]
  daySummaries?: DaySummary[]
  coachSaw?: CoachSawItem[]
  capturedGoals?: CapturedGoal[]
  capturedStates?: CapturedState[]
  capturedConstraints?: CapturedState[]
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

export async function analyseStravaActivities(
  activities: StravaActivity[],
  authToken: string,
  maxHeartRate?: number,
  currentFTP?: number,
  source?: 'strava' | 'intervals'
): Promise<AnalyseActivitiesResult> {
  const raw = await apiFetch<AnalyseActivitiesResult>('/ai/analyse-activities', {
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
    planUpdates: raw.planUpdates,
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
  /** The last rebuild the app made by itself (ai-trainer-ops#206). */
  lastRebuild?: PlanRebuild | null
}

export async function fetchPlanInputsChanged(authToken: string): Promise<PlanInputsChanged> {
  return apiFetch<PlanInputsChanged>('/ai/plan-inputs-changed', { token: authToken })
}

/** What ``POST /ai/plan-rebuild`` did (ai-trainer-ops#206). */
export interface PlanRebuildResult {
  rebuilt: boolean
  reasons: string[]
  plan?: TrainingDay[] | null
  /** A change waits: today's rebuilds are used up. */
  limited?: boolean
}

/** Rebuild the plan if the athlete changed what it was built from. The server
 *  decides; nothing happens when only rides or answers moved. */
export async function rebuildPlanIfChanged(authToken: string): Promise<PlanRebuildResult> {
  return apiFetch<PlanRebuildResult>('/ai/plan-rebuild', { token: authToken, method: 'POST', body: {} })
}

export async function askTrainer(
  question: string,
  authToken: string,
  options: AskTrainerOptions = {}
): Promise<AskTrainerResult> {
  const result = await apiFetch<BackendAskTrainerResult>('/ai/ask-trainer', {
    token: authToken,
    method: 'POST',
    signal: options.signal,
    body: {
      question,
      contextWorkout: options.contextWorkout,
      alreadyInChat: options.alreadyInChat,
    },
  })

  return {
    response: result.response,
    planUpdates: result.planUpdates,
    updatedPlan: result.updatedPlan,
    sources: result.sources,
    rideLabelUpdates: result.rideLabelUpdates,
    physiologyRationale: result.physiologyRationale,
    contextRationale: result.contextRationale,
    objectiveRationale: result.objectiveRationale,
    capturedGoals: result.capturedGoals ?? [],
    capturedStates: result.capturedStates ?? [],
    capturedConstraints: result.capturedConstraints ?? [],
    capturedDayPreferences: result.capturedDayPreferences ?? [],
    coachSaw: result.coachSaw ?? [],
    notes: result.notes ?? [],
    daySummaries: result.daySummaries ?? [],
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
  /** Which texts are still missing, as a key: the same texts, the same key
   *  (ai-trainer-ops#215). Null when none are. */
  missingKey?: string | null
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
