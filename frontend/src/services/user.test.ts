import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import {
  fetchCurrentUser,
  fetchTrainingPlan,
  fetchWorkoutLogs,
  saveTrainingPlan,
  saveWorkoutLog,
  fetchChatHistory,
  clearChatHistoryRemote,
  fetchCoachMemory,
  saveCoachMemoryRemote,
  deleteCurrentUser,
  recalculateMetrics,
  estimateFTP,
} from './user'
import type { TrainingDay } from '../store/useAppStore'

function makeDay(date: string): TrainingDay {
  return {
    date,
    workoutType: 'endurance',
    title: 'Easy Ride',
    description: 'Z2 ride',
    durationMinutes: 90,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('fetchCurrentUser', () => {
  it('maps backend response fields to the frontend profile shape', async () => {
    mockApiFetch.mockResolvedValue({
      id: 'user-1',
      email: 'alice@example.com',
      name: 'Alice',
      isOnboarded: true,
      stravaAnalysisComplete: false,
      lastStravaActivityId: null,
      bikeType: 'road',
      trainingGoal: 'ftp_improvement',
      weeklyHours: 10,
      followsTrainingPlan: true,
      fitnessLevel: 'intermediate',
      aiProvider: 'openai',
      riderAssessment: null,
      stravaConnection: null,
    })

    const result = await fetchCurrentUser('tok-123')

    expect(result.profile.name).toBe('Alice')
    expect(result.profile.email).toBe('alice@example.com')
    expect(result.profile.bikeType).toBe('road')
    expect(result.isOnboarded).toBe(true)
    expect(result.aiProvider).toBe('openai')
    expect(result.riderAssessment).toBeNull()
    expect(result.stravaConnection).toBeNull()
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me', { token: 'tok-123' })
  })

  it('applies default values for optional fields', async () => {
    mockApiFetch.mockResolvedValue({
      id: 'user-2',
      email: 'bob@example.com',
      isOnboarded: false,
      stravaAnalysisComplete: false,
      followsTrainingPlan: false,
      aiProvider: 'gemini',
    })

    const result = await fetchCurrentUser('tok-456')

    expect(result.profile.name).toBe('')
    expect(result.profile.bikeType).toBe('road')
    expect(result.profile.trainingGoal).toBe('general_fitness')
    expect(result.profile.weeklyHours).toBe(8)
    expect(result.profile.fitnessLevel).toBe('intermediate')
    expect(result.lastStravaActivityId).toBeNull()
  })
})

describe('fetchTrainingPlan', () => {
  it('returns the plan array from the backend', async () => {
    const day = makeDay('2024-05-01')
    mockApiFetch.mockResolvedValue({ plan: [day] })

    const result = await fetchTrainingPlan('tok-123')

    expect(result).toHaveLength(1)
    expect(result[0].date).toBe('2024-05-01')
  })
})

describe('saveTrainingPlan', () => {
  it('posts the plan and returns the saved plan', async () => {
    const day = makeDay('2024-05-01')
    mockApiFetch.mockResolvedValue({ plan: [day] })

    const result = await saveTrainingPlan('tok-123', [day])

    expect(result).toHaveLength(1)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/plan', {
      token: 'tok-123',
      method: 'PUT',
      body: { plan: [day] },
    })
  })
})

describe('fetchWorkoutLogs', () => {
  it('returns workout logs keyed by date', async () => {
    const logs = {
      '2024-05-01': {
        actualDurationMinutes: 60,
        perceivedEffort: 3 as const,
        notes: '',
        completedAt: '2024-05-01T10:00:00Z',
      },
    }
    mockApiFetch.mockResolvedValue(logs)

    const result = await fetchWorkoutLogs('tok-123')

    expect(result['2024-05-01'].actualDurationMinutes).toBe(60)
  })
})

describe('saveWorkoutLog', () => {
  it('posts the workout feedback for the given date', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await saveWorkoutLog('tok-123', '2024-05-01', {
      actualDurationMinutes: 90,
      perceivedEffort: 4,
      notes: 'Good session',
      completedAt: '2024-05-01T10:00:00Z',
    })

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/workouts/2024-05-01', {
      token: 'tok-123',
      method: 'POST',
      body: expect.objectContaining({ feedback: expect.anything() }),
    })
  })
})

describe('fetchChatHistory', () => {
  it('returns the list of chat messages', async () => {
    mockApiFetch.mockResolvedValue({
      messages: [{ role: 'user', content: 'Hello', timestamp: '2024-05-01T09:00:00Z' }],
    })

    const result = await fetchChatHistory('tok-123')

    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('Hello')
  })
})

describe('clearChatHistoryRemote', () => {
  it('calls DELETE on the chat endpoint', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await clearChatHistoryRemote('tok-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/chat', {
      token: 'tok-123',
      method: 'DELETE',
    })
  })
})

describe('fetchCoachMemory', () => {
  it('returns the memory string from the backend', async () => {
    mockApiFetch.mockResolvedValue({ memory: 'Athlete prefers morning rides.' })

    const result = await fetchCoachMemory('tok-123')

    expect(result).toBe('Athlete prefers morning rides.')
  })
})

describe('saveCoachMemoryRemote', () => {
  it('puts the memory and returns the updated string', async () => {
    mockApiFetch.mockResolvedValue({ memory: 'Updated memory.' })

    const result = await saveCoachMemoryRemote('tok-123', 'Updated memory.')

    expect(result).toBe('Updated memory.')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/coach-memory', {
      token: 'tok-123',
      method: 'PUT',
      body: { memory: 'Updated memory.' },
    })
  })
})

describe('deleteCurrentUser', () => {
  it('calls DELETE on the user endpoint', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await deleteCurrentUser('tok-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me', {
      token: 'tok-123',
      method: 'DELETE',
    })
  })
})

describe('recalculateMetrics', () => {
  it('posts to /users/me/recalculate-metrics without an FTP override', async () => {
    mockApiFetch.mockResolvedValue({ updated: 15, ftpUsed: 250 })

    const result = await recalculateMetrics('tok-123')

    expect(result).toEqual({ updated: 15, ftpUsed: 250 })
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/recalculate-metrics', {
      token: 'tok-123',
      method: 'POST',
      body: { ftpOverride: null },
    })
  })

  it('includes the FTP override when provided', async () => {
    mockApiFetch.mockResolvedValue({ updated: 20, ftpUsed: 270 })

    const result = await recalculateMetrics('tok-123', 270)

    expect(result).toEqual({ updated: 20, ftpUsed: 270 })
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/recalculate-metrics', {
      token: 'tok-123',
      method: 'POST',
      body: { ftpOverride: 270 },
    })
  })
})

describe('estimateFTP', () => {
  it('posts maxHeartRate and returns the estimate', async () => {
    mockApiFetch.mockResolvedValue({ estimatedFTP: 248, source: 'ftp_estimation' })

    const result = await estimateFTP('tok-123', { maxHeartRate: 185 })

    expect(result).toEqual({ estimatedFTP: 248, source: 'ftp_estimation' })
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/estimate-ftp', {
      token: 'tok-123',
      method: 'POST',
      body: { maxHeartRate: 185, restingHeartRate: null },
    })
  })

  it('sends null for omitted maxHeartRate', async () => {
    mockApiFetch.mockResolvedValue({ estimatedFTP: null, source: 'none' })

    const result = await estimateFTP('tok-123', {})

    expect(result.estimatedFTP).toBeNull()
    expect(result.source).toBe('none')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/estimate-ftp', {
      token: 'tok-123',
      method: 'POST',
      body: { maxHeartRate: null, restingHeartRate: null },
    })
  })
})

