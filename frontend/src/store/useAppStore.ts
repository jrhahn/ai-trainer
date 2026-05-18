import { create } from 'zustand'

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
  workoutType: 'rest' | 'endurance' | 'intervals' | 'tempo' | 'race' | 'recovery' | 'strength'
  title: string
  description: string
  durationMinutes: number
  targetPower?: { low: number; high: number }
  targetHeartRate?: { low: number; high: number }
  intervals?: Array<{ duration: number; power: number; rest: number }>
  completed?: boolean
  feedback?: WorkoutFeedback
  coachFeedback?: string
  workoutPurpose?: string
  keyFocusPoints?: string[]
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
}

export interface StravaConnection {
  athleteId: number
  athleteName: string
}

export interface StravaActivity {
  id: number
  name: string
  type: string
  sport_type?: string
  distance: number
  moving_time: number
  elapsed_time: number
  total_elevation_gain: number
  start_date: string
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
  activityName?: string | null
  activityStartDatetime?: string | null
  activityDate: string
  sportType: string
  tss?: number
  ctlAfter?: number
  atlAfter?: number
  tsbAfter?: number
  durationSeconds?: number
  avgPowerW?: number
  normalizedPowerW?: number
  coachNote?: string | null
  userNote?: string | null
  planMatchStatus?: 'unmatched' | 'auto_matched' | 'ambiguous' | 'manual_matched'
  matchedPlanDate?: string | null
  matchedPlanSnapshot?: Partial<TrainingDay> | null
  matchedAt?: string | null
}

export interface RiderAssessment {
  riderType: 'timetrial' | 'sprinter' | 'climber' | 'allrounder' | 'endurance'
  notes: string
  hrZones?: HrZones
  rideInsights?: string
  lastRideFeedback?: string
  loginSummary?: string
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
  riderAssessment: RiderAssessment | null
  stravaAnalysisComplete: boolean
  lastStravaActivityId: number | null
  aiProvider: AiProvider
  isOnboarded: boolean
  chatHistory: ChatMessage[]
  coachMemory: string
  raceEvents: RaceEvent[]
  metricsHistory: AthleteMetricSnapshot[]
  rideMetricsHistory: RideMetricPoint[]
  pendingFeedbackRideIds: number[]

  setAuthToken: (token: string | null) => void
  loadUserData: (tokenOverride?: string) => Promise<void>
  logout: () => void
  setUserProfile: (profile: UserProfile) => void
  setTrainingPlan: (plan: TrainingDay[]) => void
  logWorkout: (date: string, feedback: WorkoutFeedback) => void
  setStravaConnection: (connection: StravaConnection | null) => void
  setRiderAssessment: (assessment: RiderAssessment | null) => void
  setStravaAnalysisComplete: (v: boolean) => void
  setLastStravaActivityId: (id: number | null) => void
  setAiProvider: (provider: AiProvider) => void
  setOnboarded: (v: boolean) => void
  updateTrainingDay: (date: string, updates: Partial<TrainingDay>) => void
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
  setRideMetricsHistory: (history: RideMetricPoint[]) => void
  addPendingFeedbackRide: (id: number) => void
  clearPendingFeedbackRides: () => void
  toggleExpertMode: () => void
}

const dataState = {
  userProfile: null as UserProfile | null,
  trainingPlan: [] as TrainingDay[],
  workoutLogs: {} as Record<string, WorkoutFeedback>,
  stravaConnection: null as StravaConnection | null,
  riderAssessment: null as RiderAssessment | null,
  stravaAnalysisComplete: false,
  lastStravaActivityId: null as number | null,
  aiProvider: 'openai' as AiProvider,
  isOnboarded: false,
  chatHistory: [] as ChatMessage[],
  coachMemory: '',
  raceEvents: [] as RaceEvent[],
  metricsHistory: [] as AthleteMetricSnapshot[],
  rideMetricsHistory: [] as RideMetricPoint[],
  pendingFeedbackRideIds: [] as number[],
}

const initialState = {
  authToken: null as string | null,
  isLoadingUserData: false,
  loadingStep: 0,
  ...dataState,
}

function mergePlanWithWorkouts(
  plan: TrainingDay[],
  workoutLogs: Record<string, WorkoutFeedback>
): TrainingDay[] {
  return plan.map((day) => {
    const feedback = workoutLogs[day.date]
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
    logout: () => {
      persistToken(null)
      set(initialState)
    },
    setUserProfile: (profile) => set({ userProfile: profile }),
    setTrainingPlan: (plan) => set({ trainingPlan: plan }),
    logWorkout: (date, feedback) =>
      set((state) => ({
        workoutLogs: { ...state.workoutLogs, [date]: feedback },
        trainingPlan: state.trainingPlan.map((day) =>
          day.date === date ? { ...day, completed: true, feedback } : day
        ),
      })),
    setStravaConnection: (connection) => set({ stravaConnection: connection }),
    setRiderAssessment: (assessment) => set({ riderAssessment: assessment }),
    setStravaAnalysisComplete: (v) => set({ stravaAnalysisComplete: v }),
    setLastStravaActivityId: (id) => set({ lastStravaActivityId: id }),
    setAiProvider: (provider) => set({ aiProvider: provider }),
    setOnboarded: (v) => set({ isOnboarded: v }),
    updateTrainingDay: (date, updates) =>
      set((state) => ({
        trainingPlan: state.trainingPlan.map((day) =>
          day.date === date ? { ...day, ...updates } : day
        ),
      })),
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
    setRideMetricsHistory: (history) => set({ rideMetricsHistory: history }),
    addPendingFeedbackRide: (id) =>
      set((state) => ({
        pendingFeedbackRideIds: state.pendingFeedbackRideIds.includes(id)
          ? state.pendingFeedbackRideIds
          : [...state.pendingFeedbackRideIds, id],
      })),
    clearPendingFeedbackRides: () => set({ pendingFeedbackRideIds: [] }),
    toggleExpertMode: () =>
      set((state) => {
        const next = !state.isExpertMode
        persistExpertMode(next)
        return { isExpertMode: next }
      }),
    loadUserData: async (tokenOverride) => {
      const token = tokenOverride ?? get().authToken
      if (!token) return

      set({ isLoadingUserData: true, loadingStep: 0, authToken: token })
      const step = () => set((s) => ({ loadingStep: s.loadingStep + 1 }))
      try {
        const track = <T>(p: Promise<T>): Promise<T> => p.then((v) => { step(); return v })
        const [user, plan, workoutLogs, chatHistory, coachMemory, raceEvents, metricsHistory, rideMetricsHistory] = await Promise.all([
          track(fetchCurrentUser(token)),
          track(fetchTrainingPlan(token)),
          track(fetchWorkoutLogs(token)),
          track(fetchChatHistory(token)),
          track(fetchCoachMemory(token)),
          track(fetchRaceEvents(token)),
          track(fetchMetricsHistory(token)),
          track(fetchRideMetricsHistory(token)),
        ])

        set({
          authToken: token,
          userProfile: user.profile,
          trainingPlan: mergePlanWithWorkouts(plan, workoutLogs),
          workoutLogs,
          stravaConnection: user.stravaConnection,
          riderAssessment: user.riderAssessment,
          stravaAnalysisComplete: user.stravaAnalysisComplete,
          lastStravaActivityId: user.lastStravaActivityId ?? null,
          aiProvider: user.aiProvider,
          isOnboarded: user.isOnboarded,
          chatHistory,
          coachMemory,
          raceEvents,
          metricsHistory,
          rideMetricsHistory,
        })
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to load user data'
        if (/missing bearer token|invalid token|token expired|user not found/i.test(message)) {
          persistToken(null)
          set(initialState)
        }
        throw error
      } finally {
        set({ isLoadingUserData: false, loadingStep: 0 })
      }
    },
  })
)
