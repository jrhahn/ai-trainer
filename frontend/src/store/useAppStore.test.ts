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
  it('loads available data and sets dataLoadWarning when non-critical requests fail', async () => {
    mockFetchCurrentUser.mockResolvedValue({
      profile: { name: 'Alice', email: 'alice@example.com', bikeType: 'road', trainingGoal: 'general_fitness', followsTrainingPlan: true, fitnessLevel: 'intermediate' },
      isOnboarded: true,
      stravaAnalysisComplete: false,
      stravaAutoSyncEnabled: true,
      intervalsAutoSyncEnabled: true,
      aiProvider: 'openai',
    })
    mockFetchTrainingPlan.mockResolvedValue([mockDay])
    mockFetchWorkoutLogs.mockResolvedValue({})
    mockFetchChatHistory.mockRejectedValue(new Error('Network error'))
    mockFetchCoachMemory.mockRejectedValue(new Error('Network error'))
    mockFetchRaceEvents.mockResolvedValue([])
    mockFetchMetricsHistory.mockResolvedValue([])
    mockFetchRideMetricsHistory.mockResolvedValue([])

    await useAppStore.getState().loadUserData('token-123')

    const state = useAppStore.getState()
    // Core profile still loaded
    expect(state.userProfile?.email).toBe('alice@example.com')
    expect(state.trainingPlan).toHaveLength(1)
    // Failed slices fall back to empty defaults
    expect(state.chatHistory).toEqual([])
    expect(state.coachMemory).toBe('')
    // Warning is surfaced
    expect(state.dataLoadWarning).toMatch(/chat history/)
    expect(state.dataLoadWarning).toMatch(/coach memory/)
    expect(state.isLoadingUserData).toBe(false)
  })

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

describe('simple setters', () => {
  it('setAuthToken updates the token', () => {
    useAppStore.getState().setAuthToken('tok-xyz')
    expect(useAppStore.getState().authToken).toBe('tok-xyz')
  })

  it('setCoachMemory, setAiProvider and setOnboarded update state', () => {
    useAppStore.getState().setCoachMemory('remember this')
    useAppStore.getState().setAiProvider('gemini')
    useAppStore.getState().setOnboarded(true)
    const s = useAppStore.getState()
    expect(s.coachMemory).toBe('remember this')
    expect(s.aiProvider).toBe('gemini')
    expect(s.isOnboarded).toBe(true)
  })

  it('updates the strava/intervals sync and analysis flags', () => {
    const a = useAppStore.getState()
    a.setStravaAnalysisComplete(true)
    a.setLastStravaActivityId(999)
    a.setStravaAutoSyncEnabled(false)
    a.setIntervalsAnalysisComplete(true)
    a.setLastIntervalsActivityId(42)
    a.setIntervalsAutoSyncEnabled(false)
    const s = useAppStore.getState()
    expect(s.stravaAnalysisComplete).toBe(true)
    expect(s.lastStravaActivityId).toBe(999)
    expect(s.stravaAutoSyncEnabled).toBe(false)
    expect(s.intervalsAnalysisComplete).toBe(true)
    expect(s.lastIntervalsActivityId).toBe(42)
    expect(s.intervalsAutoSyncEnabled).toBe(false)
  })

  it('clearDataLoadWarning resets the warning', () => {
    useAppStore.setState({ dataLoadWarning: 'oops' })
    useAppStore.getState().clearDataLoadWarning()
    expect(useAppStore.getState().dataLoadWarning).toBeNull()
  })

  it('setPendingCoachMessage stores and clears the message', () => {
    useAppStore.getState().setPendingCoachMessage('coach says hi')
    expect(useAppStore.getState().pendingCoachMessage).toBe('coach says hi')
    useAppStore.getState().setPendingCoachMessage(null)
    expect(useAppStore.getState().pendingCoachMessage).toBeNull()
  })
})

describe('race event actions', () => {
  const event = (id: string) => ({ id, date: '2026-06-01', distanceKm: 100, elevationM: 1200 }) as never

  it('sets, adds, updates and removes race events', () => {
    const a = useAppStore.getState()
    a.setRaceEvents([event('r1')])
    expect(useAppStore.getState().raceEvents).toHaveLength(1)

    a.addRaceEvent(event('r2'))
    expect(useAppStore.getState().raceEvents.map((e) => e.id)).toEqual(['r1', 'r2'])

    a.updateRaceEvent({ id: 'r1', date: '2026-07-01', distanceKm: 200, elevationM: 2400 } as never)
    expect(useAppStore.getState().raceEvents.find((e) => e.id === 'r1')?.distanceKm).toBe(200)

    a.removeRaceEvent('r1')
    expect(useAppStore.getState().raceEvents.map((e) => e.id)).toEqual(['r2'])
  })
})

describe('updateRideMetricLabel', () => {
  it('sets a label override on the matching ride only', () => {
    const rides: RideMetricPoint[] = [
      { stravaActivityId: 1, activityDate: '2024-05-01', sportType: 'cycling' },
      { stravaActivityId: 2, activityDate: '2024-05-02', sportType: 'cycling' },
    ]
    useAppStore.getState().setRideMetricsHistory(rides)
    useAppStore.getState().updateRideMetricLabel(2, 'Race')

    const history = useAppStore.getState().rideMetricsHistory
    expect(history.find((r) => r.stravaActivityId === 2)?.labelOverride).toBe('Race')
    expect(history.find((r) => r.stravaActivityId === 1)?.labelOverride).toBeUndefined()
  })
})

describe('toggleExpertMode', () => {
  it('flips the expert-mode flag', () => {
    const initial = useAppStore.getState().isExpertMode
    useAppStore.getState().toggleExpertMode()
    expect(useAppStore.getState().isExpertMode).toBe(!initial)
    useAppStore.getState().toggleExpertMode()
    expect(useAppStore.getState().isExpertMode).toBe(initial)
  })
})

describe('logout', () => {
  it('clears the session back to initial state', () => {
    useAppStore.setState({ authToken: 'tok', isOnboarded: true })
    useAppStore.getState().logout()
    expect(useAppStore.getState().authToken).toBeNull()
    expect(useAppStore.getState().isOnboarded).toBe(false)
  })
})

describe('loadUserData auth handling', () => {
  it('clears the session on an auth error from the user endpoint', async () => {
    mockFetchCurrentUser.mockRejectedValue(new Error('Token expired'))
    mockFetchTrainingPlan.mockResolvedValue([])
    mockFetchWorkoutLogs.mockResolvedValue({})
    mockFetchChatHistory.mockResolvedValue([])
    mockFetchCoachMemory.mockResolvedValue('')
    mockFetchRaceEvents.mockResolvedValue([])
    mockFetchMetricsHistory.mockResolvedValue([])
    mockFetchRideMetricsHistory.mockResolvedValue([])

    await useAppStore.getState().loadUserData('tok-expired')

    expect(useAppStore.getState().authToken).toBeNull()
    expect(useAppStore.getState().isLoadingUserData).toBe(false)
  })

  it('surfaces a warning on a non-auth error without clearing the token', async () => {
    mockFetchCurrentUser.mockRejectedValue(new Error('Server exploded'))
    mockFetchTrainingPlan.mockResolvedValue([])
    mockFetchWorkoutLogs.mockResolvedValue({})
    mockFetchChatHistory.mockResolvedValue([])
    mockFetchCoachMemory.mockResolvedValue('')
    mockFetchRaceEvents.mockResolvedValue([])
    mockFetchMetricsHistory.mockResolvedValue([])
    mockFetchRideMetricsHistory.mockResolvedValue([])

    await useAppStore.getState().loadUserData('tok-1')

    expect(useAppStore.getState().dataLoadWarning).toMatch(/Failed to load your profile/)
  })
})
