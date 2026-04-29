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
  fetchRaceEventFeedback,
  generateTrainingPlan,
  rateCompletedWorkout,
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

// profile is kept for other usage in the file but no longer sent to AI endpoints
void profile

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MAX_CONVERSATION_HISTORY', () => {
  it('is 20', () => {
    expect(MAX_CONVERSATION_HISTORY).toBe(20)
  })
})

describe('analyseStravaActivities', () => {
  it('calls the backend analyse endpoint and returns {assessment, planUpdates}', async () => {
    mockApiFetch.mockResolvedValue({
      assessment: {
        riderType: 'allrounder',
        notes: 'Balanced rider',
        rideInsights: 'Good endurance base.',
        lastRideFeedback: 'Great ride! You held 188W for 90 min. Next session try some tempo work.',
      },
      planUpdates: [],
    })

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

    expect(result.assessment.riderType).toBe('allrounder')
    expect(result.assessment.rideInsights).toBe('Good endurance base.')
    expect(result.assessment.lastRideFeedback).toBe('Great ride! You held 188W for 90 min. Next session try some tempo work.')
    expect(result.planUpdates).toEqual([])
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

    const result = await generateTrainingPlan('token-123')

    expect(result).toHaveLength(1)
    expect(result[0].date).toBe('2024-05-01')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/generate-plan', {
      token: 'token-123',
      method: 'POST',
      body: {},
    })
  })
})

describe('adaptTrainingPlan', () => {
  it('returns the adapted plan from the backend', async () => {
    const updatedDay = { ...makeDay('2024-05-02'), durationMinutes: 45 }
    mockApiFetch.mockResolvedValue([updatedDay])

    const result = await adaptTrainingPlan([], 'token-123')

    expect(result[0].durationMinutes).toBe(45)
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/adapt-plan', {
      token: 'token-123',
      method: 'POST',
      body: { recentFeedback: [] },
    })
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

    const result = await askTrainer('How should I train?', 'token-123')

    expect(result.response).toBe('Try interval training twice a week.')
    expect(result.planUpdates?.[0].workoutType).toBe('rest')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/ask-trainer', {
      token: 'token-123',
      method: 'POST',
      body: {
        question: 'How should I train?',
        contextWorkout: undefined,
      },
    })
  })

  it('forwards sources returned by the backend', async () => {
    const sources = [
      { title: 'Polarized Training Study', doi: '10.1/test', sourceType: 'paper' },
    ]
    mockApiFetch.mockResolvedValue({
      response: 'Polarized training works well.',
      sources,
    })

    const result = await askTrainer('Tell me about polarized training', 'token-123')

    expect(result.sources).toEqual(sources)
  })
})

describe('fetchRaceEventFeedback', () => {
  it('returns coach feedback for a race event', async () => {
    const event = {
      id: 'race-1',
      date: '2026-06-01',
      startTime: null,
      distanceKm: 120,
      elevationM: 1800,
    }
    mockApiFetch.mockResolvedValue({ feedback: 'Fits well; add more climbing.' })

    const result = await fetchRaceEventFeedback(event, 'token-123', 'added')

    expect(result).toBe('Fits well; add more climbing.')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/race-event-feedback', {
      token: 'token-123',
      method: 'POST',
      body: { event, action: 'added' },
    })
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

    const result = await rateCompletedWorkout(completedDay, 'token-123')

    expect(result).toBe('Great session! You matched the plan well.')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/rate-workout', {
      token: 'token-123',
      method: 'POST',
      body: { day: completedDay },
    })
  })
})
