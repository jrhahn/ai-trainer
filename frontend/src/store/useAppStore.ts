import { create } from 'zustand'
import type { ReportedSession } from '../utils/reportedSession'
import { sessionSlot, sessionsForDate } from '../utils/planSessions'
import { endSession } from '../services/auth'

/**
 * Where the JWT used to live, before the session moved into an HttpOnly cookie
 * (ai-trainer-ops#45). Nothing reads it any more; the store removes it once, so
 * a token that script could read does not outlive the change in an open tab.
 */
const LEGACY_SESSION_TOKEN_KEY = 'ai_trainer_auth_token'

function forgetLegacyToken(): void {
  try {
    sessionStorage.removeItem(LEGACY_SESSION_TOKEN_KEY)
  } catch {
    // sessionStorage may be unavailable in some contexts; nothing to forget then
  }
}

const EXPERT_MODE_KEY = 'ai_trainer_expert_mode'
const UI_LANGUAGE_KEY = 'ai_trainer_ui_language'

/** The interface language the athlete chose in Settings, per browser like the
 * expert mode (ai-trainer-ops#103 Q27). `auto` follows the browser. */
function readUiLanguage(): 'auto' | 'en' | 'de' {
  try {
    const stored = localStorage.getItem(UI_LANGUAGE_KEY)
    return stored === 'en' || stored === 'de' ? stored : 'auto'
  } catch {
    return 'auto'
  }
}

function persistUiLanguage(value: 'auto' | 'en' | 'de'): void {
  try {
    localStorage.setItem(UI_LANGUAGE_KEY, value)
  } catch {
    // silently ignore
  }
}

function readExpertMode(): boolean {
  try {
    return localStorage.getItem(EXPERT_MODE_KEY) === 'true'
  } catch {
    return false
  }
}

function persistExpertMode(value: boolean): void {
  try {
    localStorage.setItem(EXPERT_MODE_KEY, value ? 'true' : 'false')
  } catch {
    // silently ignore
  }
}
import {
  fetchChatHistory,
  fetchCoachMemory,
  fetchCurrentUser,
  fetchMetricsHistory,
  fetchRaceEvents,
  fetchRideMetricsHistory,
  fetchTrainingPlan,
  fetchWeatherForecast,
  fetchWorkoutLogs,
} from '../services/user'

export type AiProvider = 'openai' | 'gemini'

export type Sport = 'cycling' | 'running' | 'strength'
export type Weekday = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday'

/** A rebuild of the plan the app made by itself (ai-trainer-ops#206). */
export interface PlanRebuild {
  /** ISO time of the rebuild. */
  at: string
  /** Codes from ``plan_inputs.REASONS``. */
  reasons: string[]
}

/** A weekday the athlete prefers over another (ai-trainer-ops#204). */
export interface DayPreference {
  prefer: Weekday
  over: Weekday
}

export interface UserProfile {
  name: string
  email: string
  bikeType: 'road' | 'mtb' | 'gravel' | 'other'
  trainingGoal: 'race' | 'general_fitness'
  raceDate?: string
  raceDescription?: string
  weeklyHours?: number
  /** Asked at onboarding (ai-trainer-ops#43). Undefined means not stated. */
  sports?: Sport[]
  trainingDays?: Weekday[]
  followsTrainingPlan: boolean
  maxHeartRate?: number
  restingHeartRate?: number
  currentFTP?: number
  /** Running threshold pace in seconds per kilometre — what FTP is to the bike
   * (#716). Undefined means the athlete has not set one, and their runs are
   * priced against the Critical Speed fit over their history instead. */
  thresholdPaceSecondsPerKm?: number
  /** Body weight in kilograms, optional (ai-trainer-ops#188): what a climb
   * target in W/kg needs. Undefined means the athlete has not entered one. */
  weightKg?: number
  /** Weekdays preferred over others, said in chat (ai-trainer-ops#204). */
  dayPreferences?: DayPreference[]
  consumedTokens?: number
  /** None until the athlete picks one (ai-trainer-ops#214). */
  fitnessLevel: 'beginner' | 'intermediate' | 'advanced' | null
}

export interface WorkoutFeedback {
  actualDurationMinutes: number
  averagePower?: number
  averageHeartRate?: number
  peakPower?: number
  perceivedEffort: 1 | 2 | 3 | 4 | 5
  notes: string
  completedAt: string
  /** The sport the session was logged in; the server always says (#714). */
  sport?: string
  /** How far it went, when the athlete said (ai-trainer-ops#47). `null` is
   *  "they did not say", which is not zero kilometres. */
  distanceKm?: number | null
  /** The head unit's normalized power, when the athlete said (ai-trainer-ops#169). */
  normalizedPower?: number | null
}

export interface TrainingDay {
  date: string
  /**
   * Ordered position of this session within its date (#496). A date may hold
   * more than one session (AM yoga + PM endurance), so `(date, slot)` — not the
   * date alone — identifies a session. Absent on legacy single-session days and
   * read as 0; use utils/planSessions helpers rather than reading it directly.
   */
  slot?: number
  /** Free-text when-in-the-day hint ("am", "pm", "18:30"); display only. */
  timeOfDay?: string
  /**
   * Which sport the session prescribes (#710). `workoutType` says what the
   * session is *for*; this says what the athlete actually does. Absent on every
   * day written before the field existed, and on every cycling day — the
   * backend omits the default so storage stays byte-stable. Read it through
   * utils/planSport, never directly, so a missing value reads as cycling.
   */
  sport?: string
  workoutType: 'rest' | 'endurance' | 'intervals' | 'tempo' | 'race' | 'recovery' | 'strength'
  title: string
  description: string
  durationMinutes: number
  /**
   * Optional planned-duration window (#368). A single value is the degenerate
   * window durationMinMinutes === durationMaxMinutes; when both are set,
   * durationMinutes is the midpoint. Use utils/planDuration helpers to read them.
   */
  durationMinMinutes?: number
  durationMaxMinutes?: number
  targetPower?: { low: number; high: number }
  targetHeartRate?: { low: number; high: number }
  intervals?: Array<{ duration: number; power?: number; heartRate?: number; pace?: string; rest: number }>
  /**
   * What to lift on a strength session (#714). The gym's counterpart to
   * `intervals`, and deliberately a separate field: an interval is a duration at
   * a power, a set is reps at a load.
   *
   * Intensity is `rir` (reps in reserve) by preference — the plan cannot know
   * what a given weight will feel like on the day, so RIR is the prescription
   * the athlete can execute honestly whether they are fresh or tired.
   * `percentE1rm` is the alternative for a percentage-based block. The backend's
   * persist gate keeps at most one of the two, so both are never set together.
   * Absent on every day that is not a prescribed gym session.
   */
  strengthExercises?: Array<{
    exercise: string
    sets: number
    reps: number
    rir?: number
    percentE1rm?: number
  }>
  completed?: boolean
  feedback?: WorkoutFeedback
  coachFeedback?: string
  workoutPurpose?: string
  keyFocusPoints?: string[]
  /** Hard planning priors this day still breaks (ai-trainer-ops#87), as keys. */
  priorNotes?: string[]
  /**
   * Server-set marker of which trigger last set this day. "user" means a manual
   * edit / coach-chat change, which pins the day against automated overwrites
   * (see backend services/plan_pipeline.py). Read-only from the client.
   */
  source?: string
}

export interface RaceEvent {
  id: string
  date: string
  startTime?: string | null
  distanceKm: number
  elevationM: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  planUpdateCount?: number
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  failedUserMessage?: string
  physiologyRationale?: string
  contextRationale?: string
  objectiveRationale?: string
  // Live only, never stored with the chat: the confirm step for what this
  // reply's turn captured or proposes (ai-trainer-ops#65, #71).
  capturedGoals?: Array<{ id: string; label: string }>
  capturedStates?: Array<{ id: string; label: string; removable: boolean }>
  capturedConstraints?: Array<{ id: string; label: string; removable: boolean }>
  // Live only: a day preference the chat kept (ai-trainer-ops#204).
  capturedDayPreferences?: DayPreference[]
  raceProposal?: { raceDate?: string | null; raceDescription?: string | null; calendarRaces?: number } | null
  // A race the athlete said is coming, offered for the calendar (ai-trainer-ops#175),
  // and the race it replaces when they said it moves.
  raceOffer?: {
    date: string
    replaces?: { date: string; kind: 'profile' | 'calendar'; eventId?: string | null } | null
  } | null
  profileProposal?: {
    weeklyHours?: number | null
    trainingDays?: Weekday[] | null
    sports?: Sport[] | null
    ftp?: number | null
    // ai-trainer-ops#202
    fitnessLevel?: UserProfile['fitnessLevel'] | null
    maxHeartRate?: number | null
    // ai-trainer-ops#204: the days swap in a day generally preferred.
    standingDaySwap?: boolean
  } | null
  // Live only as well: a session the athlete reported in this turn, offered
  // for entry under the reply (ai-trainer-ops#166).
  reportedSession?: ReportedSession
  // Live only as well: the deterministic blocks the coach was given (ops#17).
  coachSaw?: Array<{
    key: string
    title: string
    text: string
    source: { path: string; line: number; symbol: string; ref: string }
  }>
}

export interface StravaConnection {
  athleteId: number
  athleteName: string
}

export interface IntervalsConnection {
  athleteId: string
  athleteName?: string | null
}

export interface StravaActivity {
  id: number
  // Raw provider id (e.g. intervals.icu `i166933341`) as a string. The numeric
  // `id` above is a 19-digit hash for intervals activities and loses precision
  // as a JS Number; `external_id` is round-trip-safe and is what the backend
  // persists as the activity's external key (#429 Bug B).
  external_id?: string
  name: string
  type: string
  sport_type?: string
  distance: number
  moving_time: number
  elapsed_time: number
  total_elevation_gain: number
  start_date: string
  start_date_local?: string
  start_latlng?: [number, number] | number[]
  average_watts?: number
  weighted_average_watts?: number
  max_watts?: number
  average_heartrate?: number
  max_heartrate?: number
}

export interface HrZone {
  low: number
  high: number
}

export interface HrZones {
  zone1: HrZone
  zone2: HrZone
  zone3: HrZone
  zone4: HrZone
  zone5: HrZone
}

export interface AthleteMetricSnapshot {
  recordedAt: string
  ftp?: number
  thresholdHR?: number
  ctl?: number
  atl?: number
  tsb?: number
  /** The main sport's CTL, which `tsb` is the form of. `ctl` is the rung of
   *  whatever was logged last, about 1 after a rider's gym session. */
  formCtl?: number | null
  source: string
}

export interface RideMetricPoint {
  stravaActivityId: number
  externalActivityId?: string | null
  /** Where the row came from; `logged` is a session the athlete entered (#47). */
  activitySource?: string | null
  activityName?: string | null
  activityStartDatetime?: string | null
  activityDate: string
  sportType: string
  tss?: number
  // Where `tss` came from: 'provider' | 'power' | 'heart_rate' | 'duration'.
  // Sessions without a power meter carry an estimated load rather than a zero,
  // and an estimate shown as a measured TSS is the same mistake one layer up (#579).
  tssSource?: string | null
  ctlAfter?: number
  atlAfter?: number
  tsbAfter?: number
  durationSeconds?: number
  startLat?: number | null
  startLng?: number | null
  weatherTemperatureC?: number | null
  weatherApparentTemperatureC?: number | null
  weatherCondition?: string | null
  weatherCode?: number | null
  weatherWindSpeedKph?: number | null
  weatherPrecipitationMm?: number | null
  weatherSource?: string | null
  avgPowerW?: number
  /** The session's average heart rate; scores heart-rate intervals (ai-trainer-ops#133). */
  avgHeartRateBpm?: number | null
  /** How far it went; with the duration it gives the pace a run is scored on (#133). */
  distanceKm?: number | null
  normalizedPowerW?: number
  coachNote?: string | null
  userNote?: string | null
  feelLegs?: 'fresh' | 'normal' | 'heavy' | null
  labelOverride?: string | null
  ridePurpose?: string | null
  classificationConfidence?: string | null
  /**
   * The "what was this session?" question the coach puts on the activity (#580).
   * `purposeQuestionOpen` is decided on the backend so the rule for what counts
   * as unresolved lives in one place; `purposeQuestionStatus` records how it was
   * closed once it has been.
   */
  purposeQuestionOpen?: boolean
  purposeQuestionStatus?: 'answered' | 'skipped' | null
  /**
   * The compliance badge scored on the backend against the matched plan day.
   * Authoritative when present — the same value the coach sees, so the card and
   * the chat can no longer disagree (#551). Absent for unmatched rides, where
   * the local computation below still applies.
   */
  matchScore?: number | null
  matchLabel?: string | null
  planMatchStatus?: 'unmatched' | 'auto_matched' | 'ambiguous' | 'manual_matched'
  matchedPlanDate?: string | null
  matchedPlanSnapshot?: Partial<TrainingDay> | null
  matchedAt?: string | null
}

/**
 * One day of the upcoming outlook near the athlete's training location (#495).
 * Keyed by ISO date so planned days can look their own forecast up; days beyond
 * the ~16-day horizon are simply absent.
 */
export interface DailyForecast {
  date: string
  condition?: string | null
  weatherCode?: number | null
  temperatureMaxC?: number | null
  temperatureMinC?: number | null
  precipitationMm?: number | null
  windSpeedKph?: number | null
  /** Coaching-relevant summary: very_hot | hot | cold | freezing | rain | … */
  loadFlag?: string | null
}

export interface AthleteHomeLocation {
  latitude: number
  longitude: number
  label: string
  /** user_set beats inferred; latest_ride means nothing is persisted yet. */
  source: string
  confidence: number
  rideCount?: number
  updatedAt?: string | null
}

export interface RiderAssessment {
  riderType: 'timetrial' | 'sprinter' | 'climber' | 'allrounder' | 'endurance'
  notes: string
  hrZones?: HrZones
  rideInsights?: string
  lastRideFeedback?: string
  loginSummary?: string
  /** Coach-authored dashboard status badge (#499) — written by the backend
   * status pipeline, never computed here, so the coach can explain it. */
  trainingStatusLabel?: string
  /** Mirrors `TRAINING_STATUS_TONES` in backend/services/prompts.py. The
   *  dashboard coloured the training summary from it (#623) until the owner's
   *  review of 2026-10-24; it now shows the label in words and ignores this. */
  trainingStatusTone?: 'positive' | 'steady' | 'caution' | 'alert'
  trainingStatusRationale?: string
}

interface AppState {
  authToken: string | null
  isLoadingUserData: boolean
  loadingStep: number
  isExpertMode: boolean
  uiLanguage: 'auto' | 'en' | 'de'
  userProfile: UserProfile | null
  trainingPlan: TrainingDay[]
  /** The plan's English texts in the interface language, by the English text
   * (ai-trainer-ops#121). The plan itself stays English. */
  planTranslations: Record<string, string>
  workoutLogs: Record<string, WorkoutFeedback>
  stravaConnection: StravaConnection | null
  intervalsConnection: IntervalsConnection | null
  /** Whether this server can connect Strava at all (ai-trainer-ops#131). */
  stravaAvailable: boolean
  riderAssessment: RiderAssessment | null
  /** Advisory warning when the stored FTP is implausible against the
   * athlete's maximal aerobic power.  Null when the pair looks sane. */
  ftpPlausibilityWarning: string | null
  stravaAnalysisComplete: boolean
  lastStravaActivityId: number | null
  stravaAutoSyncEnabled: boolean
  intervalsAnalysisComplete: boolean
  lastIntervalsActivityId: number | null
  intervalsAutoSyncEnabled: boolean
  aiProvider: AiProvider
  isOnboarded: boolean
  chatHistory: ChatMessage[]
  coachMemory: string
  raceEvents: RaceEvent[]
  metricsHistory: AthleteMetricSnapshot[]
  rideMetricsHistory: RideMetricPoint[]
  /** Upcoming forecast keyed by ISO date, for the weather shown on planned days. */
  weatherForecast: Record<string, DailyForecast>
  homeLocation: AthleteHomeLocation | null
  pendingFeedbackRideIds: number[]
  dataLoadWarning: string | null
  /** Which loads failed, by their English name; the banner translates them (ai-trainer-ops#121). */
  dataLoadFailures: string[]
  /** The profile could not be loaded for a reason other than the session
   * (ai-trainer-ops#118). Unknown is not "not onboarded": the app shows a retry
   * rather than onboarding, which overwrote the race and the plan. */
  profileLoadFailed: boolean

  setAuthToken: (token: string | null) => void
  clearDataLoadWarning: () => void
  /** Says something failed, in the banner the data-load warning uses, translated by the caller (ai-trainer-ops#272). */
  showWarning: (message: string) => void
  loadUserData: (tokenOverride?: string) => Promise<void>
  logout: () => void
  setUserProfile: (profile: UserProfile) => void
  setTrainingPlan: (plan: TrainingDay[]) => void
  /** Adds translations to the ones held; keyed by the English text, an old one is never wrong, only unused. */
  mergePlanTranslations: (translations: Record<string, string>) => void
  /** Log feedback for one session; `slot` defaults to the day's first (#496). */
  logWorkout: (date: string, feedback: WorkoutFeedback, slot?: number) => void
  setStravaConnection: (connection: StravaConnection | null) => void
  setIntervalsConnection: (connection: IntervalsConnection | null) => void
  setRiderAssessment: (assessment: RiderAssessment | null) => void
  setFtpPlausibilityWarning: (warning: string | null) => void
  setStravaAnalysisComplete: (v: boolean) => void
  setLastStravaActivityId: (id: number | null) => void
  setStravaAutoSyncEnabled: (enabled: boolean) => void
  setIntervalsAnalysisComplete: (v: boolean) => void
  setLastIntervalsActivityId: (id: number | null) => void
  setIntervalsAutoSyncEnabled: (enabled: boolean) => void
  setAiProvider: (provider: AiProvider) => void
  setOnboarded: (v: boolean) => void
  /**
   * Patch one planned session. `slot` picks which session on `date` when the day
   * holds a two-a-day (#496); omit it to patch the day's first session, which is
   * the only one a single-session day has.
   */
  updateTrainingDay: (
    date: string,
    updates: Partial<TrainingDay>,
    slot?: number
  ) => void
  resetAll: () => void
  addChatMessage: (msg: ChatMessage) => void
  setCoachMemory: (memory: string) => void
  setRaceEvents: (events: RaceEvent[]) => void
  addRaceEvent: (event: RaceEvent) => void
  updateRaceEvent: (event: RaceEvent) => void
  removeRaceEvent: (eventId: string) => void
  setChatHistory: (history: ChatMessage[]) => void
  clearChatHistory: () => void
  setMetricsHistory: (history: AthleteMetricSnapshot[]) => void
  setWeatherForecast: (days: DailyForecast[]) => void
  setHomeLocation: (location: AthleteHomeLocation | null) => void
  setRideMetricsHistory: (history: RideMetricPoint[]) => void
  updateRideMetric: (ride: RideMetricPoint) => void
  updateRideMetricLabel: (stravaActivityId: number, labelOverride: string) => void
  updateRideMetricLegs: (
    stravaActivityId: number,
    feelLegs: 'fresh' | 'normal' | 'heavy' | null,
  ) => void
  addPendingFeedbackRide: (id: number) => void
  clearPendingFeedbackRides: () => void
  removePendingFeedbackRides: (ids: number[]) => void
  toggleExpertMode: () => void
  setUiLanguage: (language: 'auto' | 'en' | 'de') => void
  pendingCoachMessage: string | null
  setPendingCoachMessage: (msg: string | null) => void
  /** The rebuild the app just made by itself, and why (ai-trainer-ops#206). */
  planRebuild: PlanRebuild | null
  setPlanRebuild: (rebuild: PlanRebuild | null) => void
}

const dataState = {
  userProfile: null as UserProfile | null,
  trainingPlan: [] as TrainingDay[],
  planTranslations: {} as Record<string, string>,
  workoutLogs: {} as Record<string, WorkoutFeedback>,
  stravaConnection: null as StravaConnection | null,
  intervalsConnection: null as IntervalsConnection | null,
  stravaAvailable: true,
  riderAssessment: null as RiderAssessment | null,
  ftpPlausibilityWarning: null as string | null,
  stravaAnalysisComplete: false,
  lastStravaActivityId: null as number | null,
  stravaAutoSyncEnabled: true,
  intervalsAnalysisComplete: false,
  lastIntervalsActivityId: null as number | null,
  intervalsAutoSyncEnabled: true,
  aiProvider: 'gemini' as AiProvider,
  isOnboarded: false,
  chatHistory: [] as ChatMessage[],
  coachMemory: '',
  raceEvents: [] as RaceEvent[],
  metricsHistory: [] as AthleteMetricSnapshot[],
  rideMetricsHistory: [] as RideMetricPoint[],
  weatherForecast: {} as Record<string, DailyForecast>,
  homeLocation: null as AthleteHomeLocation | null,
  pendingFeedbackRideIds: [] as number[],
  pendingCoachMessage: null as string | null,
  planRebuild: null as PlanRebuild | null,
  dataLoadWarning: null as string | null,
  dataLoadFailures: [] as string[],
  profileLoadFailed: false,
}

const initialState = {
  authToken: null as string | null,
  isLoadingUserData: false,
  loadingStep: 0,
  ...dataState,
}

/**
 * The key a session's workout log is stored under (#496).
 *
 * The first session of a date keeps the bare date, so every log written before
 * two-a-days existed still resolves; only the extra sessions add a `#slot`
 * suffix. This mirrors the backend's `GET /users/me/workouts` keying exactly.
 */
export function workoutLogKey(date: string, slot?: number): string {
  const resolved = sessionSlot({ date, slot })
  return resolved === 0 ? date : `${date}#${resolved}`
}

/** The slot an update targets: the given one, or the day's first session. */
function targetSlot(
  plan: TrainingDay[],
  date: string,
  slot: number | undefined
): number {
  if (slot !== undefined) return slot
  const sessions = sessionsForDate(plan, date)
  return sessions.length > 0 ? sessionSlot(sessions[0]) : 0
}

function mergePlanWithWorkouts(
  plan: TrainingDay[],
  workoutLogs: Record<string, WorkoutFeedback>
): TrainingDay[] {
  return plan.map((day) => {
    const feedback = workoutLogs[workoutLogKey(day.date, day.slot)]
    return feedback ? { ...day, completed: true, feedback } : day
  })
}

// The load `loadUserData` is running, so a deduped call can wait for it rather
// than return before the data exists (ai-trainer-ops#58).
let inFlightLoad: { token: string; promise: Promise<void> } | null = null

export const useAppStore = create<AppState>()(
  (set, get) => ({
    ...initialState,    // Expert mode preference — persisted in localStorage, survives tab close.
    // NOT part of initialState so that resetAll() / logout() does not clear it.
    isExpertMode: readExpertMode(),
    // Same reasoning: a sign-out does not change which language this browser reads.
    uiLanguage: readUiLanguage(),    // Null until App has asked the server whether a cookie session exists
    // (`resumeSession`, ai-trainer-ops#45).
    authToken: (forgetLegacyToken(), null),

    setAuthToken: (token) => set({ authToken: token }),
    clearDataLoadWarning: () => set({ dataLoadWarning: null, dataLoadFailures: [] }),
    showWarning: (message) => set({ dataLoadWarning: message, dataLoadFailures: [] }),
    logout: () => {
      void endSession()
      set(initialState)
    },
    setUserProfile: (profile) => set({ userProfile: profile }),
    setTrainingPlan: (plan) => set({ trainingPlan: plan }),
    mergePlanTranslations: (translations) =>
      set((state) => ({ planTranslations: { ...state.planTranslations, ...translations } })),
    logWorkout: (date, feedback, slot) =>
      set((state) => {
        const target = targetSlot(state.trainingPlan, date, slot)
        return {
          workoutLogs: {
            ...state.workoutLogs,
            [workoutLogKey(date, target)]: feedback,
          },
          trainingPlan: state.trainingPlan.map((day) =>
            day.date === date && sessionSlot(day) === target
              ? { ...day, completed: true, feedback }
              : day
          ),
        }
      }),
    setStravaConnection: (connection) => set({ stravaConnection: connection }),
    setIntervalsConnection: (connection) => set({ intervalsConnection: connection }),
    setRiderAssessment: (assessment) => set({ riderAssessment: assessment }),
    setFtpPlausibilityWarning: (warning) => set({ ftpPlausibilityWarning: warning }),
    setStravaAnalysisComplete: (v) => set({ stravaAnalysisComplete: v }),
    setLastStravaActivityId: (id) => set({ lastStravaActivityId: id }),
    setStravaAutoSyncEnabled: (enabled) => set({ stravaAutoSyncEnabled: enabled }),
    setIntervalsAnalysisComplete: (v) => set({ intervalsAnalysisComplete: v }),
    setLastIntervalsActivityId: (id) => set({ lastIntervalsActivityId: id }),
    setIntervalsAutoSyncEnabled: (enabled) => set({ intervalsAutoSyncEnabled: enabled }),
    setAiProvider: (provider) => set({ aiProvider: provider }),
    setOnboarded: (v) => set({ isOnboarded: v }),
    updateTrainingDay: (date, updates, slot) =>
      set((state) => {
        const target = targetSlot(state.trainingPlan, date, slot)
        return {
          trainingPlan: state.trainingPlan.map((day) =>
            day.date === date && sessionSlot(day) === target
              ? { ...day, ...updates }
              : day
          ),
        }
      }),
    resetAll: () => {
      void endSession()
      set(initialState)
    },
    addChatMessage: (msg) =>
      set((state) => ({ chatHistory: [...state.chatHistory, msg] })),
    setCoachMemory: (memory) => set({ coachMemory: memory }),
    setRaceEvents: (events) => set({ raceEvents: events }),
    addRaceEvent: (event) =>
      set((state) => ({ raceEvents: [...state.raceEvents, event] })),
    updateRaceEvent: (event) =>
      set((state) => ({
        raceEvents: state.raceEvents.map((item) => (item.id === event.id ? event : item)),
      })),
    removeRaceEvent: (eventId) =>
      set((state) => ({
        raceEvents: state.raceEvents.filter((event) => event.id !== eventId),
      })),
    setChatHistory: (history) => set({ chatHistory: history }),
    clearChatHistory: () => set({ chatHistory: [] }),
    setMetricsHistory: (history) => set({ metricsHistory: history }),
    setWeatherForecast: (days) =>
      set({
        weatherForecast: Object.fromEntries(days.map((day) => [day.date, day])),
      }),
    setHomeLocation: (location) => set({ homeLocation: location }),
    setRideMetricsHistory: (history) => set({ rideMetricsHistory: history }),
    updateRideMetric: (ride) =>
      set((state) => ({
        rideMetricsHistory: state.rideMetricsHistory.map((existing) =>
          existing.stravaActivityId === ride.stravaActivityId ? ride : existing
        ),
      })),
    updateRideMetricLabel: (stravaActivityId, labelOverride) =>
      set((state) => ({
        rideMetricsHistory: state.rideMetricsHistory.map((ride) =>
          ride.stravaActivityId === stravaActivityId
            ? { ...ride, labelOverride }
            : ride
        ),
      })),
    updateRideMetricLegs: (stravaActivityId, feelLegs) =>
      set((state) => ({
        rideMetricsHistory: state.rideMetricsHistory.map((ride) =>
          ride.stravaActivityId === stravaActivityId
            ? { ...ride, feelLegs }
            : ride
        ),
      })),
    addPendingFeedbackRide: (id) =>
      set((state) => ({
        pendingFeedbackRideIds: state.pendingFeedbackRideIds.includes(id)
          ? state.pendingFeedbackRideIds
          : [...state.pendingFeedbackRideIds, id],
      })),
    clearPendingFeedbackRides: () => set({ pendingFeedbackRideIds: [] }),
    removePendingFeedbackRides: (ids) =>
      set((state) => {
        const remove = new Set(ids)
        return {
          pendingFeedbackRideIds: state.pendingFeedbackRideIds.filter(
            (id) => !remove.has(id)
          ),
        }
      }),
    setPendingCoachMessage: (msg) => set({ pendingCoachMessage: msg }),
    setPlanRebuild: (rebuild) => set({ planRebuild: rebuild }),
    setUiLanguage: (language) => {
      persistUiLanguage(language)
      set({ uiLanguage: language })
    },
    toggleExpertMode: () =>
      set((state) => {
        const next = !state.isExpertMode
        persistExpertMode(next)
        return { isExpertMode: next }
      }),
    loadUserData: (tokenOverride) => {
      const token = tokenOverride ?? get().authToken
      if (!token) return Promise.resolve()
      // Dedupe concurrent loads for the same token: the App effect loads on every
      // authToken change while the login pages also call loadUserData(token)
      // directly, which otherwise fires two full parallel loads (#458). The
      // skipped caller gets the load that is already running, so awaiting this
      // always means "the data is loaded", never "somebody else started loading
      // it" — a sign-in page that awaits it and then navigates must not land on
      // an empty store (ai-trainer-ops#58).
      if (get().isLoadingUserData && get().authToken === token) {
        return inFlightLoad?.token === token ? inFlightLoad.promise : Promise.resolve()
      }

      const promise = (async () => {
        set({
          isLoadingUserData: true,
          loadingStep: 0,
          authToken: token,
          dataLoadWarning: null,
          dataLoadFailures: [],
          profileLoadFailed: false,
        })
        const step = () => set((s) => ({ loadingStep: s.loadingStep + 1 }))
        const track = <T>(p: Promise<T>): Promise<T> => p.then((v) => { step(); return v })

        const [userResult, planResult, workoutLogsResult, chatHistoryResult, coachMemoryResult, raceEventsResult, metricsHistoryResult, rideMetricsHistoryResult, weatherForecastResult] =
          await Promise.allSettled([
            track(fetchCurrentUser(token)),
            track(fetchTrainingPlan(token)),
            track(fetchWorkoutLogs(token)),
            track(fetchChatHistory(token)),
            track(fetchCoachMemory(token)),
            track(fetchRaceEvents(token)),
            track(fetchMetricsHistory(token)),
            track(fetchRideMetricsHistory(token)),
            track(fetchWeatherForecast(token)),
          ])

        // If the session was torn down while we were loading (manual logout, or an
        // AUTH_EXPIRED_EVENT from a 401 on one of the parallel fetches), do not
        // resurrect it by writing these now-stale results back. The logout /
        // re-login that changed the token owns the resulting state (#454).
        if (get().authToken !== token) {
          return
        }

        // Auth errors from the user endpoint must clear the session — app can't continue.
        if (userResult.status === 'rejected') {
          const message = userResult.reason instanceof Error ? userResult.reason.message : ''
          if (/missing bearer token|invalid token|token expired|user not found/i.test(message)) {
            void endSession()
            set({ ...initialState, isLoadingUserData: false })
          } else {
            set({ isLoadingUserData: false, loadingStep: 0, profileLoadFailed: true })
          }
          return
        }

        const user = userResult.value
        const workoutLogs = workoutLogsResult.status === 'fulfilled' ? workoutLogsResult.value : {}
        const plan = planResult.status === 'fulfilled' ? planResult.value : []

        const failed: string[] = []
        if (planResult.status === 'rejected') failed.push('training plan')
        if (workoutLogsResult.status === 'rejected') failed.push('workout logs')
        if (chatHistoryResult.status === 'rejected') failed.push('chat history')
        if (coachMemoryResult.status === 'rejected') failed.push('coach memory')
        if (raceEventsResult.status === 'rejected') failed.push('race events')
        if (metricsHistoryResult.status === 'rejected') failed.push('fitness metrics')
        if (rideMetricsHistoryResult.status === 'rejected') failed.push('ride history')
        // The forecast is decoration on top of the plan, not data the dashboard
        // needs to function, so a failed lookup stays silent rather than nagging.
        const weather =
          weatherForecastResult.status === 'fulfilled'
            ? weatherForecastResult.value
            : { location: null, days: [] }

        set({
          authToken: token,
          userProfile: user.profile,
          trainingPlan: mergePlanWithWorkouts(plan, workoutLogs),
          workoutLogs,
          stravaConnection: user.stravaConnection,
          intervalsConnection: user.intervalsConnection,
          stravaAvailable: user.stravaAvailable,
          riderAssessment: user.riderAssessment,
          ftpPlausibilityWarning: user.ftpPlausibilityWarning,
          stravaAnalysisComplete: user.stravaAnalysisComplete,
          lastStravaActivityId: user.lastStravaActivityId ?? null,
          stravaAutoSyncEnabled: user.stravaAutoSyncEnabled,
          intervalsAnalysisComplete: user.intervalsAnalysisComplete,
          lastIntervalsActivityId: user.lastIntervalsActivityId ?? null,
          intervalsAutoSyncEnabled: user.intervalsAutoSyncEnabled,
          aiProvider: user.aiProvider,
          isOnboarded: user.isOnboarded,
          chatHistory: chatHistoryResult.status === 'fulfilled' ? chatHistoryResult.value : [],
          coachMemory: coachMemoryResult.status === 'fulfilled' ? coachMemoryResult.value : '',
          raceEvents: raceEventsResult.status === 'fulfilled' ? raceEventsResult.value : [],
          metricsHistory: metricsHistoryResult.status === 'fulfilled' ? metricsHistoryResult.value : [],
          rideMetricsHistory: rideMetricsHistoryResult.status === 'fulfilled' ? rideMetricsHistoryResult.value : [],
          weatherForecast: Object.fromEntries(weather.days.map((day) => [day.date, day])),
          homeLocation: weather.location,
          dataLoadWarning: failed.length > 0
            ? `Some data failed to load (${failed.join(', ')}). Refresh the page to retry.`
            : null,
          dataLoadFailures: failed,
          isLoadingUserData: false,
          loadingStep: 0,
        })
      })()
      inFlightLoad = { token, promise }
      const release = () => {
        if (inFlightLoad?.promise === promise) inFlightLoad = null
      }
      promise.then(release, release)
      return promise
    },
  })
)
