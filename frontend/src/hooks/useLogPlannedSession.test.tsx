import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { plannedWorkoutMatchesActivity, useLogPlannedSession } from './useLogPlannedSession'
import { useAppStore } from '../store/useAppStore'
import type { StravaActivity, TrainingDay, WorkoutFeedback } from '../store/useAppStore'

const { mockSave, mockRate, mockFetchPlan, mockSavePlan } = vi.hoisted(() => ({
  mockSave: vi.fn(),
  mockRate: vi.fn(),
  mockFetchPlan: vi.fn(),
  mockSavePlan: vi.fn(),
}))

vi.mock('../services/user', () => ({
  saveWorkoutLog: mockSave,
  fetchTrainingPlan: mockFetchPlan,
  saveTrainingPlan: mockSavePlan,
}))
vi.mock('../services/ai', () => ({ rateCompletedWorkout: mockRate }))

const morning: TrainingDay = { date: '2026-10-09', slot: 0, workoutType: 'endurance', title: 'Ride', durationMinutes: 60 } as TrainingDay
const evening: TrainingDay = { date: '2026-10-09', slot: 1, workoutType: 'strength', title: 'Gym', durationMinutes: 45 } as TrainingDay
const feedback: WorkoutFeedback = {
  actualDurationMinutes: 55,
  perceivedEffort: 3,
  notes: '',
  completedAt: '2026-10-09T18:00:00',
} as WorkoutFeedback

let queryClient: QueryClient
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
)

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  useAppStore.getState().resetAll()
  useAppStore.setState({
    authToken: 'tok',
    userProfile: { name: 'A', email: 'a@x', bikeType: 'road', trainingGoal: 'general_fitness', weeklyHours: 6, followsTrainingPlan: false, fitnessLevel: 'intermediate' },
    trainingPlan: [morning, evening],
  })
  vi.clearAllMocks()
  mockSave.mockResolvedValue(undefined)
  mockRate.mockResolvedValue({ feedback: '' })
  mockFetchPlan.mockResolvedValue([morning, evening])
  mockSavePlan.mockResolvedValue(undefined)
})

describe('useLogPlannedSession (ai-trainer-ops#52)', () => {
  it('marks the session done at once, saves it, and asks for a rating', async () => {
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(evening, feedback))

    expect(useAppStore.getState().trainingPlan[1].completed).toBe(true)
    expect(mockSave).toHaveBeenCalledWith('tok', '2026-10-09', feedback, 1)
    await waitFor(() => expect(mockRate).toHaveBeenCalled())
  })

  it("writes the coach's note onto the logged session's own slot only", async () => {
    mockRate.mockResolvedValue({ feedback: 'Solid gym session.' })
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(evening, feedback))

    await waitFor(() => expect(mockSavePlan).toHaveBeenCalled())
    const saved = mockSavePlan.mock.calls[0][1] as TrainingDay[]
    expect(saved[1].coachFeedback).toBe('Solid gym session.')
    expect(saved[0].coachFeedback).toBeUndefined()
    expect(result.current.rating?.feedback).toBe('Solid gym session.')
  })

  it('keeps the log when the server plan comes back without it', async () => {
    // Only ride matching marks a plan day completed on the server, so the plan
    // fetched after the rating does not know about a hand-logged session.
    mockRate.mockResolvedValue({ feedback: 'Solid gym session.' })
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(evening, feedback))

    await waitFor(() => expect(mockSavePlan).toHaveBeenCalled())
    const after = useAppStore.getState().trainingPlan
    expect(after[1]).toMatchObject({ completed: true, feedback, coachFeedback: 'Solid gym session.' })
    expect(after[0].completed).toBeFalsy()
  })

  it('keeps an earlier log on another session of the day as well', async () => {
    mockRate.mockResolvedValue({ feedback: 'Good.' })
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(morning, feedback))
    await act(() => result.current.logSession(evening, feedback))

    await waitFor(() => expect(mockSavePlan).toHaveBeenCalledTimes(2))
    const after = useAppStore.getState().trainingPlan
    expect(after[0].completed).toBe(true)
    expect(after[1].completed).toBe(true)
  })

  it("keeps the server's feedback for a session a sync ticked without a local log", async () => {
    const serverFeedback = { ...feedback, notes: 'from the server' }
    useAppStore.setState({ trainingPlan: [{ ...morning, completed: true }, evening] })
    mockFetchPlan.mockResolvedValue([{ ...morning, feedback: serverFeedback }, evening])
    mockRate.mockResolvedValue({ feedback: 'Good.' })
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(evening, feedback))

    await waitFor(() => expect(mockSavePlan).toHaveBeenCalled())
    expect(useAppStore.getState().trainingPlan[0]).toMatchObject({ completed: true, feedback: serverFeedback })
  })

  it('keeps the optimistic log when the network fails', async () => {
    mockSave.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(morning, feedback))

    expect(useAppStore.getState().trainingPlan[0].completed).toBe(true)
  })

  it('saves nothing to the server without a session', async () => {
    useAppStore.setState({ authToken: null })
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(morning, feedback))

    expect(mockSave).not.toHaveBeenCalled()
    expect(mockRate).not.toHaveBeenCalled()
  })

  it('hands the rating a matching cached activity, so it can read its streams', async () => {
    queryClient.setQueryData(['stravaActivities', 'tok'], [
      { id: 7, type: 'Run', start_date: '2026-10-09T07:00:00Z' },
      { id: 9, type: 'Ride', start_date_local: '2026-10-09T07:00:00' },
    ] as StravaActivity[])
    const { result } = renderHook(() => useLogPlannedSession(), { wrapper })
    await act(() => result.current.logSession(morning, feedback))

    await waitFor(() => expect(mockRate).toHaveBeenCalled())
    expect(mockRate.mock.calls[0][2]).toBe(9)
  })
})

describe('plannedWorkoutMatchesActivity', () => {
  const activity = (type: string) => ({ id: 1, type, start_date: '' }) as StravaActivity
  it.each([
    [morning, 'Ride', true],
    [morning, 'cycling', true],
    [morning, 'Run', false],
    [evening, 'WeightTraining', true],
    [evening, 'Workout', true],
    [evening, 'Ride', false],
  ])('%#: %s vs %s', (day, type, expected) => {
    expect(plannedWorkoutMatchesActivity(day, activity(type))).toBe(expected)
  })
})

it('reads an activity with no type at all as matching nothing', () => {
  const untyped = { id: 1, start_date: '' } as StravaActivity
  expect(plannedWorkoutMatchesActivity(morning, untyped)).toBe(false)
  expect(plannedWorkoutMatchesActivity(evening, untyped)).toBe(false)
})

