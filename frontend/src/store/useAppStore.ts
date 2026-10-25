import { create } from 'zustand'
import { sessionSlot, sessionsForDate } from '../utils/planSessions'
import { endSession } from '../services/auth'
import { readLocal, writeLocal } from '../lib/storage'

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
  const stored = readLocal(UI_LANGUAGE_KEY)
  return stored === 'en' || stored === 'de' ? stored : 'auto'
}

function persistUiLanguage(value: 'auto' | 'en' | 'de'): void {
  writeLocal(UI_LANGUAGE_KEY, value)
}

function readExpertMode(): boolean {
  return readLocal(EXPERT_MODE_KEY) === 'true'
}

function persistExpertMode(value: boolean): void {
  writeLocal(EXPERT_MODE_KEY, value ? 'true' : 'false')
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

import type {
  AiProvider,
  PlanRebuild,
  UserProfile,
  WorkoutFeedback,
  TrainingDay,
  RaceEvent,
  ChatMessage,
  StravaConnection,
  IntervalsConnection,
  AthleteMetricSnapshot,
  RideMetricPoint,
  DailyForecast,
  AthleteHomeLocation,
  RiderAssessment,
} from '../types/domain'

export type {
  AiProvider,
  Sport,
  Weekday,
  PlanRebuild,
  DayPreference,
  UserProfile,
  WorkoutFeedback,
  TrainingDay,
  RaceEvent,
  ChatMessage,
  StravaConnection,
  IntervalsConnection,
  StravaActivity,
  HrZone,
  HrZones,
  AthleteMetricSnapshot,
  RideMetricPoint,
  DailyForecast,
  AthleteHomeLocation,
  RiderAssessment,
} from '../types/domain'

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
  /** Something failed that was not a load, already translated (ai-trainer-ops#272).
   * Its own field, so a failed action and a failed load do not hide each other
   * (review of #1068), and the cold-start note still tells only a failed load. */
  notice: string | null
  /** The profile could not be loaded for a reason other than the session
   * (ai-trainer-ops#118). Unknown is not "not onboarded": the app shows a retry
   * rather than onboarding, which overwrote the race and the plan. */
  profileLoadFailed: boolean

  setAuthToken: (token: string | null) => void
  clearDataLoadWarning: () => void
  /** Says something failed, in the banner the data-load warning uses, translated by the caller (ai-trainer-ops#272).
   * One slot: a second failure replaces the first, which a dismiss or the next
   * attempt would end anyway. It stays across a data load, since what it says
   * stays true (review of #1068). */
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
  notice: null as string | null,
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
    ...initialState,    // Expert mode preference — persisted per browser (lib/storage), survives tab close.
    // NOT part of initialState so that resetAll() / logout() does not clear it.
    isExpertMode: readExpertMode(),
    // Same reasoning: a sign-out does not change which language this browser reads.
    uiLanguage: readUiLanguage(),    // Null until App has asked the server whether a cookie session exists
    // (`resumeSession`, ai-trainer-ops#45).
    authToken: (forgetLegacyToken(), null),

    setAuthToken: (token) => set({ authToken: token }),
    clearDataLoadWarning: () => set({ dataLoadWarning: null, dataLoadFailures: [], notice: null }),
    showWarning: (message) => set({ notice: message }),
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
