import { create } from 'zustand'
import { sessionSlot, sessionsForDate } from '../utils/planSessions'

/**
 * Storage key for the JWT auth token.
 *
 * ## Why sessionStorage instead of localStorage?
 *
 * `sessionStorage` is intentionally chosen over `localStorage` for the
 * following security and functional reasons:
 *
 * - **OAuth / page-reload survival**: OAuth flows (e.g. Strava, Authelia)
 *   redirect the browser away and back. `sessionStorage` persists across
 *   same-tab page reloads and cross-origin redirects within the same tab,
 *   so the token survives the round-trip without requiring the user to log
 *   in again.
 *
 * - **Automatic session scoping**: Each browser tab gets its own independent
 *   `sessionStorage`. When the tab (or window) is closed the storage is
 *   cleared automatically — no explicit logout required.
 *
 * - **Reduced XSS blast radius compared to localStorage**: `localStorage` is
 *   shared across all tabs for the same origin and persists indefinitely,
 *   meaning a stolen token remains usable even after the user walks away
 *   from their machine. `sessionStorage` limits exposure to the lifetime of
 *   the active tab.
 *
 * ### Known tradeoff
 * Unlike `localStorage`, `sessionStorage` does **not** survive opening a new
 * tab or a fresh browser window. Users who open the app in a new tab will
 * need to re-authenticate. This is an accepted UX cost in exchange for the
 * security benefits above.
 *
 * Note: both storage APIs are equally accessible to JavaScript running on the
 * page, so neither provides protection against a successful XSS attack. The
 * primary mitigation for XSS is Content Security Policy, input sanitisation,
 * and React's built-in output escaping.
 */
const SESSION_TOKEN_KEY = 'ai_trainer_auth_token'

/**
 * Reads the persisted JWT from sessionStorage on app start-up.
 * Returns `null` if the key is absent or if sessionStorage is unavailable
 * (e.g. in privacy/incognito modes that block storage access).
 */
function readStoredToken(): string | null {
  try {
    return sessionStorage.getItem(SESSION_TOKEN_KEY)
  } catch {
    return null
  }
}

/**
 * Writes or removes the JWT in sessionStorage.
 * Pass `null` to clear the token (e.g. on logout or auth error).
 * Errors are swallowed silently — sessionStorage may be unavailable in some
 * browser contexts (private-mode restrictions, embedded iframes with
 * restrictive sandbox attributes, etc.).
 */
function persistToken(token: string | null): void {
  try {
    if (token) {
      sessionStorage.setItem(SESSION_TOKEN_KEY, token)
    } else {
      sessionStorage.removeItem(SESSION_TOKEN_KEY)
    }
  } catch {
    // sessionStorage may be unavailable in some contexts; silently ignore
  }
}

const EXPERT_MODE_KEY = 'ai_trainer_expert_mode'

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

export interface UserProfile {
  name: string
  email: string
  bikeType: 'road' | 'mtb' | 'gravel' | 'other'
  trainingGoal: 'race' | 'general_fitness'
  raceDate?: string
  raceDescription?: string
  weeklyHours?: number
  followsTrainingPlan: boolean
  maxHeartRate?: number
  restingHeartRate?: number
  currentFTP?: number
  consumedTokens?: number
  fitnessLevel: 'beginner' | 'intermediate' | 'advanced'
}

export interface WorkoutFeedback {
  actualDurationMinutes: number
  averagePower?: number
  averageHeartRate?: number
  peakPower?: number
  perceivedEffort: 1 | 2 | 3 | 4 | 5
  notes: string
  completedAt: string
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
  intervals?: Array<{ duration: number; power: number; rest: number }>
  completed?: boolean
  feedback?: WorkoutFeedback
  coachFeedback?: string
  workoutPurpose?: string
  keyFocusPoints?: string[]
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
  source: string
}

export interface RideMetricPoint {
  stravaActivityId: number
  externalActivityId?: string | null
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
  trainingStatusTone?: 'positive' | 'steady' | 'caution'
  trainingStatusRationale?: string
}

interface AppState {
  authToken: string | null
  isLoadingUserData: boolean
  loadingStep: number
  isExpertMode: boolean
  userProfile: UserProfile | null
  trainingPlan: TrainingDay[]
  workoutLogs: Record<string, WorkoutFeedback>
  stravaConnection: StravaConnection | null
  intervalsConnection: IntervalsConnection | null
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

  setAuthToken: (token: string | null) => void
  clearDataLoadWarning: () => void
  loadUserData: (tokenOverride?: string) => Promise<void>
  logout: () => void
  setUserProfile: (profile: UserProfile) => void
  setTrainingPlan: (plan: TrainingDay[]) => void
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
  pendingCoachMessage: string | null
  setPendingCoachMessage: (msg: string | null) => void
}

const dataState = {
  userProfile: null as UserProfile | null,
  trainingPlan: [] as TrainingDay[],
  workoutLogs: {} as Record<string, WorkoutFeedback>,
  stravaConnection: null as StravaConnection | null,
  intervalsConnection: null as IntervalsConnection | null,
  riderAssessment: null as RiderAssessment | null,
  ftpPlausibilityWarning: null as string | null,
  stravaAnalysisComplete: false,
  lastStravaActivityId: null as number | null,
  stravaAutoSyncEnabled: true,
  intervalsAnalysisComplete: false,
  lastIntervalsActivityId: null as number | null,
  intervalsAutoSyncEnabled: true,
  aiProvider: 'openai' as AiProvider,
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
  dataLoadWarning: null as string | null,
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

export const useAppStore = create<AppState>()(
  (set, get) => ({
    ...initialState,    // Expert mode preference — persisted in localStorage, survives tab close.
    // NOT part of initialState so that resetAll() / logout() does not clear it.
    isExpertMode: readExpertMode(),    // Hydrate authToken from sessionStorage on app load.
    // sessionStorage persists across same-tab page reloads (including OAuth
    // redirect round-trips) but is cleared when the tab is closed.
    // See the SESSION_TOKEN_KEY comment above for the full security rationale.
    authToken: readStoredToken(),

    setAuthToken: (token) => {
      persistToken(token)
      set({ authToken: token })
    },
    clearDataLoadWarning: () => set({ dataLoadWarning: null }),
    logout: () => {
      persistToken(null)
      set(initialState)
    },
    setUserProfile: (profile) => set({ userProfile: profile }),
    setTrainingPlan: (plan) => set({ trainingPlan: plan }),
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
      persistToken(null)
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
    toggleExpertMode: () =>
      set((state) => {
        const next = !state.isExpertMode
        persistExpertMode(next)
        return { isExpertMode: next }
      }),
    loadUserData: async (tokenOverride) => {
      const token = tokenOverride ?? get().authToken
      if (!token) return
      // Dedupe concurrent loads for the same token: the App effect loads on every
      // authToken change while the login pages also call loadUserData(token)
      // directly, which otherwise fires two full parallel loads. The in-flight
      // caller keeps running and the isLoadingUserData overlay covers the window
      // for whichever caller is skipped here (#458).
      if (get().isLoadingUserData && get().authToken === token) return

      set({ isLoadingUserData: true, loadingStep: 0, authToken: token, dataLoadWarning: null })
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
          persistToken(null)
          set({ ...initialState, isLoadingUserData: false })
        } else {
          set({ isLoadingUserData: false, loadingStep: 0, dataLoadWarning: 'Failed to load your profile. Please refresh the page.' })
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
        isLoadingUserData: false,
        loadingStep: 0,
      })
    },
  })
)
