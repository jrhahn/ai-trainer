import { beforeEach, describe, expect, it, vi } from 'vitest'

const {
  mockFetchCurrentUser,
  mockFetchTrainingPlan,
  mockFetchWorkoutLogs,
  mockFetchChatHistory,
  mockFetchCoachMemory,
  mockFetchRaceEvents,
  mockFetchMetricsHistory,
  mockFetchRideMetricsHistory,
} = vi.hoisted(() => ({
  mockFetchCurrentUser: vi.fn(),
  mockFetchTrainingPlan: vi.fn(),
  mockFetchWorkoutLogs: vi.fn(),
  mockFetchChatHistory: vi.fn(),
  mockFetchCoachMemory: vi.fn(),
  mockFetchRaceEvents: vi.fn(),
  mockFetchMetricsHistory: vi.fn(),
  mockFetchRideMetricsHistory: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchCurrentUser: mockFetchCurrentUser,
  fetchTrainingPlan: mockFetchTrainingPlan,
  fetchWorkoutLogs: mockFetchWorkoutLogs,
  fetchChatHistory: mockFetchChatHistory,
  fetchCoachMemory: mockFetchCoachMemory,
  fetchRaceEvents: mockFetchRaceEvents,
  fetchMetricsHistory: mockFetchMetricsHistory,
  fetchRideMetricsHistory: mockFetchRideMetricsHistory,
}))

import { useAppStore } from './useAppStore'
import type { ChatMessage, RideMetricPoint, WorkoutFeedback, TrainingDay } from './useAppStore'

const mockFeedback: WorkoutFeedback = {
  actualDurationMinutes: 60,
  perceivedEffort: 3,
  notes: 'Felt good',
  completedAt: '2024-01-15T10:00:00Z',
}

const mockDay: TrainingDay = {
  date: '2024-01-15',
  workoutType: 'endurance',
  title: 'Easy Ride',
  description: '2h easy ride at Z2',
  durationMinutes: 120,
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
})

describe('chatHistory actions', () => {
  it('starts with an empty chat history', () => {
    expect(useAppStore.getState().chatHistory).toEqual([])
  })

  it('addChatMessage appends a message to the history', () => {
    const msg: ChatMessage = { role: 'user', content: 'Hello coach!', timestamp: '2024-01-15T09:00:00Z' }
    useAppStore.getState().addChatMessage(msg)
    expect(useAppStore.getState().chatHistory).toHaveLength(1)
    expect(useAppStore.getState().chatHistory[0]).toEqual(msg)
  })

  it('clearChatHistory resets history to empty array', () => {
    const msg: ChatMessage = { role: 'user', content: 'Hi', timestamp: '2024-01-15T09:00:00Z' }
    useAppStore.getState().addChatMessage(msg)
    useAppStore.getState().clearChatHistory()
    expect(useAppStore.getState().chatHistory).toEqual([])
  })
})

describe('ride metric actions', () => {
  it('replaces a ride metric with the updated server copy', () => {
    const ride: RideMetricPoint = {
      stravaActivityId: 42,
      activityDate: '2026-06-18',
      sportType: 'Ride',
      labelOverride: 'Needs work',
    }
    useAppStore.getState().setRideMetricsHistory([ride])

    useAppStore.getState().updateRideMetric({
      ...ride,
      userNote: 'RPE 8/10 | plan match: matched plan',
      labelOverride: 'Solid',
    })

    expect(useAppStore.getState().rideMetricsHistory[0]).toEqual(
      expect.objectContaining({
        stravaActivityId: 42,
        userNote: 'RPE 8/10 | plan match: matched plan',
        labelOverride: 'Solid',
      }),
    )
  })
})

describe('coachMemory actions', () => {
  it('starts with empty coach memory', () => {
    expect(useAppStore.getState().coachMemory).toBe('')
  })

  it('setCoachMemory stores the memory string', () => {
    const notes = 'Athlete prefers morning rides.'
    useAppStore.getState().setCoachMemory(notes)
    expect(useAppStore.getState().coachMemory).toBe(notes)
  })
})

describe('resetAll', () => {
  it('resets auth and app state to initial values', () => {
    useAppStore.getState().setAuthToken('token-123')
    useAppStore.getState().setOnboarded(true)
    useAppStore.getState().setCoachMemory('some notes')
    useAppStore.getState().resetAll()

    const state = useAppStore.getState()
    expect(state.authToken).toBeNull()
    expect(state.isOnboarded).toBe(false)
    expect(state.trainingPlan).toEqual([])
    expect(state.coachMemory).toBe('')
    expect(state.raceEvents).toEqual([])
  })
})

describe('logWorkout', () => {
  it('marks the day as completed and attaches feedback', () => {
    useAppStore.getState().setTrainingPlan([mockDay])
    useAppStore.getState().logWorkout('2024-01-15', mockFeedback)

    const day = useAppStore.getState().trainingPlan.find((d) => d.date === '2024-01-15')
    expect(day?.completed).toBe(true)
    expect(day?.feedback).toEqual(mockFeedback)
  })
})

describe('updateTrainingDay', () => {
  it('applies partial updates to the matching day', () => {
    useAppStore.getState().setTrainingPlan([mockDay])
    useAppStore.getState().updateTrainingDay('2024-01-15', { title: 'Updated Title', durationMinutes: 90 })

    const day = useAppStore.getState().trainingPlan.find((d) => d.date === '2024-01-15')
    expect(day?.title).toBe('Updated Title')
    expect(day?.durationMinutes).toBe(90)
    expect(day?.workoutType).toBe('endurance')
  })
})

describe('loadUserData', () => {
  it('hydrates store state from backend services and merges workout logs into the plan', async () => {
    mockFetchCurrentUser.mockResolvedValue({
      profile: {
        name: 'Alice',
        email: 'alice@example.com',
        bikeType: 'road',
        trainingGoal: 'general_fitness',
        weeklyHours: 10,
        followsTrainingPlan: true,
        fitnessLevel: 'intermediate',
      },
      isOnboarded: true,
      stravaAnalysisComplete: true,
      stravaAutoSyncEnabled: false,
      intervalsAutoSyncEnabled: false,
      aiProvider: 'openai',
      riderAssessment: { riderType: 'allrounder', notes: 'Strong aerobic base' },
      stravaConnection: { athleteId: 7, athleteName: 'Alice Rider' },
    })
    mockFetchTrainingPlan.mockResolvedValue([mockDay])
    mockFetchWorkoutLogs.mockResolvedValue({ '2024-01-15': mockFeedback })
    mockFetchChatHistory.mockResolvedValue([{ role: 'assistant', content: 'Hi', timestamp: '2024-01-15T09:00:00Z' }])
    mockFetchCoachMemory.mockResolvedValue('Prefers morning rides.')
    mockFetchRaceEvents.mockResolvedValue([
      { id: 'race-1', date: '2024-06-01', startTime: '09:00', distanceKm: 120, elevationM: 1800 },
    ])
    mockFetchMetricsHistory.mockResolvedValue([
      { recordedAt: '2024-01-10T10:00:00Z', ftp: 260, source: 'strava_analysis' },
    ])
    mockFetchRideMetricsHistory.mockResolvedValue([
      { activityDate: '2024-01-10', sportType: 'cycling', tss: 80, ctlAfter: 45.2, atlAfter: 60.1, tsbAfter: -14.9 },
    ])

    await useAppStore.getState().loadUserData('token-123')

    const state = useAppStore.getState()
    expect(state.authToken).toBe('token-123')
    expect(state.userProfile?.email).toBe('alice@example.com')
    expect(state.trainingPlan[0].completed).toBe(true)
    expect(state.trainingPlan[0].feedback).toEqual(mockFeedback)
    expect(state.chatHistory).toHaveLength(1)
    expect(state.coachMemory).toBe('Prefers morning rides.')
    expect(state.raceEvents).toHaveLength(1)
    expect(state.raceEvents[0].distanceKm).toBe(120)
    expect(state.stravaConnection?.athleteName).toBe('Alice Rider')
    expect(state.metricsHistory).toHaveLength(1)
    expect(state.metricsHistory[0].ftp).toBe(260)
    expect(state.rideMetricsHistory).toHaveLength(1)
    expect(state.rideMetricsHistory[0].ctlAfter).toBe(45.2)
    expect(state.stravaAutoSyncEnabled).toBe(false)
    expect(state.intervalsAutoSyncEnabled).toBe(false)
  })
})
