import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AiProvider } from '../services/ai'

export interface UserProfile {
  name: string
  email: string
  bikeType: 'road' | 'mtb' | 'gravel' | 'other'
  trainingGoal: 'ftp_improvement' | 'race' | 'general_fitness' | 'weight_loss'
  raceDate?: string
  raceDescription?: string
  weeklyHours: number
  followsTrainingPlan: boolean
  restingHeartRate?: number
  maxHeartRate?: number
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
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export interface StravaTokens {
  accessToken: string
  refreshToken: string
  expiresAt: number
  athleteId: number
  athleteName: string
}

export interface StravaActivity {
  id: number
  name: string
  type: string
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

export interface RiderAssessment {
  estimatedFTP?: number
  estimatedThresholdHR?: number
  riderType: 'timetrial' | 'sprinter' | 'climber' | 'allrounder' | 'endurance'
  notes: string
}

interface AppState {
  userProfile: UserProfile | null
  trainingPlan: TrainingDay[]
  workoutLogs: Record<string, WorkoutFeedback>
  stravaTokens: StravaTokens | null
  riderAssessment: RiderAssessment | null
  stravaAnalysisComplete: boolean
  aiProvider: AiProvider
  aiApiKey: string
  isOnboarded: boolean
  chatHistory: ChatMessage[]
  coachMemory: string

  setUserProfile: (profile: UserProfile) => void
  setTrainingPlan: (plan: TrainingDay[]) => void
  logWorkout: (date: string, feedback: WorkoutFeedback) => void
  setStravaTokens: (tokens: StravaTokens | null) => void
  setRiderAssessment: (assessment: RiderAssessment | null) => void
  setStravaAnalysisComplete: (v: boolean) => void
  setAiProvider: (provider: AiProvider) => void
  setAiApiKey: (key: string) => void
  setOnboarded: (v: boolean) => void
  updateTrainingDay: (date: string, updates: Partial<TrainingDay>) => void
  resetAll: () => void
  addChatMessage: (msg: ChatMessage) => void
  setCoachMemory: (memory: string) => void
  clearChatHistory: () => void
}

const initialState = {
  userProfile: null,
  trainingPlan: [],
  workoutLogs: {},
  stravaTokens: null,
  riderAssessment: null,
  stravaAnalysisComplete: false,
  aiProvider: 'openai' as AiProvider,
  aiApiKey: '',
  isOnboarded: false,
  chatHistory: [] as ChatMessage[],
  coachMemory: '',
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      ...initialState,

      setUserProfile: (profile) => set({ userProfile: profile }),
      setTrainingPlan: (plan) => set({ trainingPlan: plan }),
      logWorkout: (date, feedback) =>
        set((state) => ({
          workoutLogs: { ...state.workoutLogs, [date]: feedback },
          trainingPlan: state.trainingPlan.map((d) =>
            d.date === date ? { ...d, completed: true, feedback } : d
          ),
        })),
      setStravaTokens: (tokens) => set({ stravaTokens: tokens }),
      setRiderAssessment: (assessment) => set({ riderAssessment: assessment }),
      setStravaAnalysisComplete: (v) => set({ stravaAnalysisComplete: v }),
      setAiProvider: (provider) => set({ aiProvider: provider }),
      setAiApiKey: (key) => set({ aiApiKey: key }),
      setOnboarded: (v) => set({ isOnboarded: v }),
      updateTrainingDay: (date, updates) =>
        set((state) => ({
          trainingPlan: state.trainingPlan.map((d) =>
            d.date === date ? { ...d, ...updates } : d
          ),
        })),
      resetAll: () => set(initialState),
      addChatMessage: (msg) =>
        set((state) => ({ chatHistory: [...state.chatHistory, msg] })),
      setCoachMemory: (memory) => set({ coachMemory: memory }),
      clearChatHistory: () => set({ chatHistory: [] }),
    }),
    { name: 'ai-trainer-store' }
  )
)
