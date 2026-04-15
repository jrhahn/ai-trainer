import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TrainingDay, UserProfile } from '../store/useAppStore'

const mockApiFetch = vi.hoisted(() => vi.fn())

vi.mock('./api', () => ({
  apiFetch: mockApiFetch,
}))

import {
  MAX_CONVERSATION_HISTORY,
  analyseStravaActivities,
  askTrainer,
  adaptTrainingPlan,
  generateTrainingPlan,
  rateCompletedWorkout,
  updateCoachMemory,
} from './ai'

const profile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  weeklyHours: 10,
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

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

describe('MAX_CONVERSATION_HISTORY', () => {
  it('is 20', () => {
    expect(MAX_CONVERSATION_HISTORY).toBe(20)
  })
})

describe('analyseStravaActivities', () => {
  it('calls the backend analyse endpoint', async () => {
    mockApiFetch.mockResolvedValue({ estimatedFTP: 280, riderType: 'allrounder', notes: 'Balanced rider' })

    const result = await analyseStravaActivities([
      {
        id: 1,
        name: 'Morning Ride',
        type: 'Ride',
        distance: 50000,
        moving_time: 3600,
        elapsed_time: 3700,
        total_elevation_gain: 500,
        start_date: '2024-05-01T10:00:00Z',
      },
    ], 'token-123')

    expect(result.estimatedFTP).toBe(280)
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/analyse-activities', {
      token: 'token-123',
      method: 'POST',
      body: { activities: expect.any(Array) },
    })
  })
})

describe('generateTrainingPlan', () => {
  it('returns the plan array from the backend', async () => {
    const planDay = makeDay('2024-05-01')
    mockApiFetch.mockResolvedValue([planDay])

    const result = await generateTrainingPlan(profile, 'token-123')

    expect(result).toHaveLength(1)
    expect(result[0].date).toBe('2024-05-01')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/generate-plan', {
      token: 'token-123',
      method: 'POST',
      body: { profile, riderAssessment: undefined },
    })
  })
})

describe('adaptTrainingPlan', () => {
  it('returns the adapted plan from the backend', async () => {
    const updatedDay = { ...makeDay('2024-05-02'), durationMinutes: 45 }
    mockApiFetch.mockResolvedValue([updatedDay])

    const result = await adaptTrainingPlan([makeDay('2024-05-02')], [], profile, 'token-123')

    expect(result[0].durationMinutes).toBe(45)
  })
})

describe('askTrainer', () => {
  it('calls the backend ask-trainer endpoint and maps snake_case plan updates', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Try interval training twice a week.',
      plan_updates: [
        { date: '2024-05-02', workoutType: 'rest', title: 'Rest', description: 'Rest up', durationMinutes: 0 },
      ],
    })

    const result = await askTrainer('How should I train?', [], profile, 'token-123')

    expect(result.response).toBe('Try interval training twice a week.')
    expect(result.planUpdates?.[0].workoutType).toBe('rest')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/ask-trainer', {
      token: 'token-123',
      method: 'POST',
      body: {
        question: 'How should I train?',
        plan: [],
        profile,
        riderAssessment: undefined,
        coachMemory: undefined,
        conversationHistory: undefined,
      },
    })
  })
})

describe('updateCoachMemory', () => {
  it('returns the updated memory text from the backend', async () => {
    mockApiFetch.mockResolvedValue({ memory: 'Athlete prefers morning rides.' })

    const result = await updateCoachMemory('', 'I like riding in the morning', 'Got it!', 'token-123')

    expect(result).toBe('Athlete prefers morning rides.')
  })
})

describe('rateCompletedWorkout', () => {
  const completedDay = {
    ...makeDay('2024-05-01'),
    completed: true,
    feedback: {
      actualDurationMinutes: 85,
      averagePower: 210,
      perceivedEffort: 3 as const,
      notes: 'Felt good',
      completedAt: '2024-05-01T10:00:00Z',
    },
  }

  it('returns the backend workout rating text', async () => {
    mockApiFetch.mockResolvedValue({ feedback: 'Great session! You matched the plan well.' })

    const result = await rateCompletedWorkout(completedDay, profile, 'token-123')

    expect(result).toBe('Great session! You matched the plan well.')
  })
})
