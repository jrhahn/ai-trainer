import { beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay, WorkoutFeedback } from '../types/domain'
import {
  getPlan,
  getWorkoutLogs,
  logWorkout,
  restoreWorkoutLog,
  setPlan,
  setPlanAndLogs,
  updatePlanDay,
  usePlan,
  workoutLogKey,
} from './plan'

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
  // Sign-out clears the query cache, and so the plan (ai-trainer-ops#273).
  useAppStore.getState().resetAll()
})

describe('logWorkout', () => {
  it('marks the day as completed and attaches feedback', () => {
    setPlan([mockDay])
    logWorkout('2024-01-15', mockFeedback)

    const day = getPlan().find((d) => d.date === '2024-01-15')
    expect(day?.completed).toBe(true)
    expect(day?.feedback).toEqual(mockFeedback)
  })

  it('logs the evening session of a two-a-day under its own key (#496)', () => {
    setPlan([
      { ...mockDay, slot: 0 },
      { ...mockDay, slot: 1, title: 'Evening' },
    ])
    logWorkout('2024-01-15', mockFeedback, 1)

    expect(getWorkoutLogs()).toEqual({ [workoutLogKey('2024-01-15', 1)]: mockFeedback })
    expect(getPlan().map((d) => d.completed ?? false)).toEqual([false, true])
  })
})

describe('updatePlanDay', () => {
  it('applies partial updates to the matching day', () => {
    setPlan([mockDay])
    updatePlanDay('2024-01-15', { title: 'Updated Title', durationMinutes: 90 })

    const day = getPlan().find((d) => d.date === '2024-01-15')
    expect(day?.title).toBe('Updated Title')
    expect(day?.durationMinutes).toBe(90)
    expect(day?.workoutType).toBe('endurance')
  })
})

describe('completion comes from the log (ai-trainer-ops#273)', () => {
  it('keeps a logged session completed when a writer replaces the plan', () => {
    // A chat reply or a rebuild sends the server's plan. Setting it was a
    // plain replace, which dropped a log the server had not taken in yet.
    setPlanAndLogs([mockDay], {})
    logWorkout('2024-01-15', mockFeedback)
    setPlan([{ ...mockDay, title: 'Rebuilt' }])

    expect(getPlan()[0]).toMatchObject({ title: 'Rebuilt', completed: true, feedback: mockFeedback })
  })

  it('puts a session back the way it was when its log is taken back', () => {
    const earlier = { ...mockFeedback, actualDurationMinutes: 40 }
    setPlanAndLogs([mockDay], { '2024-01-15': earlier })
    logWorkout('2024-01-15', mockFeedback)
    restoreWorkoutLog('2024-01-15', earlier)
    expect(getPlan()[0].feedback).toEqual(earlier)

    restoreWorkoutLog('2024-01-15', undefined)
    expect(getPlan()[0].completed).toBeUndefined()
    expect(getWorkoutLogs()).toEqual({})
  })

  it('renders a component again when a log arrives', async () => {
    setPlan([mockDay])
    const { result } = renderHook(() => usePlan())
    expect(result.current[0].completed).toBeUndefined()

    // react-query hands a cache change to its components a tick later.
    act(() => logWorkout('2024-01-15', mockFeedback))
    await waitFor(() => expect(result.current[0].completed).toBe(true))
  })

  it('is empty after sign-out', () => {
    setPlanAndLogs([mockDay], { '2024-01-15': mockFeedback })
    useAppStore.getState().resetAll()

    expect(getPlan()).toEqual([])
    expect(getWorkoutLogs()).toEqual({})
  })
})
