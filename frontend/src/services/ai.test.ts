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
  extractAthleteFacts,
  fetchRaceEventFeedback,
  fetchReadinessScore,
  fetchNextRideRecommendation,
  generateTrainingPlan,
  processPendingFeedbacks,
  rateCompletedWorkout,
  refreshAthleteModel,
  refreshLoginSummary,
  refreshTrainingStatus,
  resolveRideMatch,
} from './ai'

const profile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'general_fitness',
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

describe('refreshAthleteModel', () => {
  it('POSTs to the refresh endpoint and returns the model', async () => {
    const model = { ftpWatts: 275, summary: 'refreshed' }
    mockApiFetch.mockResolvedValue(model)

    const result = await refreshAthleteModel('tok-123')

    expect(result).toBe(model)
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/refresh-athlete-model', {
      token: 'tok-123',
      method: 'POST',
    })
  })
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

  it('includes the source when analysing Intervals.icu activities', async () => {
    mockApiFetch.mockResolvedValue({
      assessment: { riderType: 'allrounder', estimatedFTP: 250 },
    })

    await analyseStravaActivities([
      {
        id: 1,
        name: 'Intervals Ride',
        type: 'Ride',
        distance: 0,
        moving_time: 3600,
        elapsed_time: 3600,
        total_elevation_gain: 0,
        start_date: '2026-06-07T10:00:00Z',
      },
    ], 'token-123', undefined, undefined, 'intervals')

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/analyse-activities', {
      token: 'token-123',
      method: 'POST',
      body: { activities: expect.any(Array), source: 'intervals' },
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

describe('askTrainer', () => {
  it('calls the backend ask-trainer endpoint and maps snake_case plan updates', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Try interval training twice a week.',
      plan_updates: [
        { date: '2024-05-02', workoutType: 'rest', title: 'Rest', description: 'Rest up', durationMinutes: 0 },
      ],
      updated_plan: [
        { ...makeDay('2024-05-02'), workoutType: 'rest', title: 'Rest', description: 'Rest up', durationMinutes: 0 },
      ],
    })

    const result = await askTrainer('How should I train?', 'token-123')

    expect(result.response).toBe('Try interval training twice a week.')
    expect(result.planUpdates?.[0].workoutType).toBe('rest')
    expect(result.updatedPlan?.[0].workoutType).toBe('rest')
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

  it('maps physiology and context rationale (camelCase)', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'On the numbers a ride is fine, but knowing you I would rest.',
      physiologyRationale: 'fresh enough for an easy ride',
      contextRationale: 'history of overreaching favours rest',
    })

    const result = await askTrainer('Can I ride today?', 'token-123')

    expect(result.physiologyRationale).toBe('fresh enough for an easy ride')
    expect(result.contextRationale).toBe('history of overreaching favours rest')
  })

  it('maps physiology and context rationale from snake_case', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Rest today.',
      physiology_rationale: 'tolerable on the numbers',
      context_rationale: 'rest fits you better',
    })

    const result = await askTrainer('Can I ride today?', 'token-123')

    expect(result.physiologyRationale).toBe('tolerable on the numbers')
    expect(result.contextRationale).toBe('rest fits you better')
  })
})

describe('extractAthleteFacts', () => {
  it('posts the transcript and maps candidates (camelCase + snake_case)', async () => {
    mockApiFetch.mockResolvedValue({
      candidates: [
        {
          fact: 'Gets anxious after rest days',
          category: 'psychological_tendencies',
          confidence: 0.7,
          sourceSnippet: 'I feel like I lose fitness.',
        },
        {
          fact: 'Loves long climbs',
          source_snippet: 'Nothing beats a big climb.',
        },
      ],
    })

    const result = await extractAthleteFacts('Athlete: I hate resting.', 'token-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/extract-athlete-facts', {
      token: 'token-123',
      method: 'POST',
      body: { transcript: 'Athlete: I hate resting.' },
    })
    expect(result[0]).toEqual({
      fact: 'Gets anxious after rest days',
      category: 'psychological_tendencies',
      confidence: 0.7,
      sourceSnippet: 'I feel like I lose fitness.',
    })
    // Defaults fill in for a sparse candidate; snake_case snippet is mapped.
    expect(result[1].category).toBe('general')
    expect(result[1].confidence).toBe(0.35)
    expect(result[1].sourceSnippet).toBe('Nothing beats a big climb.')
  })

  it('returns an empty array when there are no candidates', async () => {
    mockApiFetch.mockResolvedValue({ candidates: [] })
    const result = await extractAthleteFacts('nothing here', 'token-123')
    expect(result).toEqual([])
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
    mockApiFetch.mockResolvedValue({
      feedback: 'Great session! You matched the plan well.',
      needs_athlete_feedback: false,
      follow_up_question: null,
      suggested_feedback_tags: [],
    })

    const result = await rateCompletedWorkout(completedDay, 'token-123')

    expect(result).toEqual({
      feedback: 'Great session! You matched the plan well.',
      needsAthleteFeedback: false,
      followUpQuestion: null,
      suggestedFeedbackTags: [],
    })
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/rate-workout', {
      token: 'token-123',
      method: 'POST',
      body: { day: completedDay },
    })
  })
})

describe('resolveRideMatch', () => {
  it('posts selected ride and returns updated ride plus plan updates', async () => {
    mockApiFetch.mockResolvedValue({
      ride: {
        stravaActivityId: 7001,
        activityDate: '2026-05-06',
        sportType: 'cycling',
        planMatchStatus: 'manual_matched',
      },
      coachNote: 'That was the planned tempo ride.',
      planUpdates: [{ date: '2026-05-07', workoutType: 'recovery', durationMinutes: 45 }],
    })

    const result = await resolveRideMatch('token-123', '2026-05-06', 7001)

    expect(result.ride.planMatchStatus).toBe('manual_matched')
    expect(result.coachNote).toBe('That was the planned tempo ride.')
    expect(result.planUpdates?.[0].workoutType).toBe('recovery')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'token-123',
      method: 'POST',
      body: { plannedDate: '2026-05-06', stravaActivityId: 7001 },
    })
  })

  it('sends the chosen session on a two-a-day', async () => {
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 7002 } })

    await resolveRideMatch('token-123', '2026-05-06', 7002, 1)

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'token-123',
      method: 'POST',
      body: { plannedDate: '2026-05-06', stravaActivityId: 7002, plannedSlot: 1 },
    })
  })

  it('sends slot 0 explicitly when it is asked to', async () => {
    // 0 is falsy, so the morning session has to survive the optional check.
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 7003 } })

    await resolveRideMatch('token-123', '2026-05-06', 7003, 0)

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'token-123',
      method: 'POST',
      body: { plannedDate: '2026-05-06', stravaActivityId: 7003, plannedSlot: 0 },
    })
  })
})

describe('fetchReadinessScore', () => {
  it('maps the snake_case backend payload to camelCase', async () => {
    mockApiFetch.mockResolvedValue({
      score: 72,
      form_score: 68,
      fitness_score: 75,
      ctl: 80,
      atl: 70,
      tsb: 10,
      days_until_race: 5,
      race_date: '2026-06-01',
      projected_score: 84,
      projected_ctl: 85,
      projected_atl: 72,
      projected_tsb: 13,
      recommendations: [
        {
          recommendation: 'Taper now',
          reasoning: [
            { source: 'coach_inference', text: 'TSB is 10.0' },
            { source: 'scientific_evidence', text: 'taper lifts form' },
          ],
        },
      ],
    })

    const result = await fetchReadinessScore('tok-123')

    expect(result).toEqual({
      score: 72,
      formScore: 68,
      fitnessScore: 75,
      ctl: 80,
      atl: 70,
      tsb: 10,
      daysUntilRace: 5,
      raceDate: '2026-06-01',
      projectedScore: 84,
      projectedCtl: 85,
      projectedAtl: 72,
      projectedTsb: 13,
      recommendations: [
        {
          recommendation: 'Taper now',
          reasoning: [
            { source: 'coach_inference', text: 'TSB is 10.0' },
            { source: 'scientific_evidence', text: 'taper lifts form' },
          ],
        },
      ],
    })
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/readiness-score', { token: 'tok-123' })
  })

  it('defaults recommendations to an empty array', async () => {
    mockApiFetch.mockResolvedValue({
      score: 50, form_score: 50, fitness_score: 50, ctl: 40, atl: 40, tsb: 0,
      days_until_race: 0, race_date: null,
    })

    const result = await fetchReadinessScore('tok-123')

    expect(result.recommendations).toEqual([])
  })

  it('falls back to coach_inference for an unknown reasoning source', async () => {
    mockApiFetch.mockResolvedValue({
      score: 50, form_score: 50, fitness_score: 50, ctl: 40, atl: 40, tsb: 0,
      days_until_race: 0, race_date: null,
      recommendations: [
        {
          recommendation: 'Keep going',
          reasoning: [{ source: 'mystery', text: 'unknown origin' }],
        },
      ],
    })

    const result = await fetchReadinessScore('tok-123')

    expect(result.recommendations[0].reasoning).toEqual([
      { source: 'coach_inference', text: 'unknown origin' },
    ])
  })
})

describe('refreshLoginSummary', () => {
  it('POSTs and returns the login summary', async () => {
    mockApiFetch.mockResolvedValue({ loginSummary: 'Welcome back!' })

    const summary = await refreshLoginSummary('tok-123')

    expect(summary).toBe('Welcome back!')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/refresh-login-summary', {
      token: 'tok-123',
      method: 'POST',
    })
  })

  it('returns an empty string when no summary is present', async () => {
    mockApiFetch.mockResolvedValue({})
    expect(await refreshLoginSummary('tok-123')).toBe('')
  })
})

describe('refreshTrainingStatus', () => {
  it('POSTs and returns the coach-authored badge', async () => {
    mockApiFetch.mockResolvedValue({
      label: 'Ahead of plan',
      tone: 'positive',
      rationale: 'You added an unplanned long ride.',
    })

    const badge = await refreshTrainingStatus('tok-123')

    expect(badge).toEqual({
      label: 'Ahead of plan',
      tone: 'positive',
      rationale: 'You added an unplanned long ride.',
    })
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/refresh-training-status', {
      token: 'tok-123',
      method: 'POST',
    })
  })

  it('returns null when the backend has no badge to give', async () => {
    mockApiFetch.mockResolvedValue({})
    expect(await refreshTrainingStatus('tok-123')).toBeNull()
  })

  it('falls back to the neutral tone when the tone is unrecognised', async () => {
    // The tone drives the chip's colour, so an unknown value must degrade to
    // something renderable rather than leaving the badge unstyled.
    mockApiFetch.mockResolvedValue({ label: 'Cruising', tone: 'euphoric' })

    expect(await refreshTrainingStatus('tok-123')).toEqual({
      label: 'Cruising',
      tone: 'steady',
      rationale: '',
    })
  })

  it('keeps the caution tone intact', async () => {
    mockApiFetch.mockResolvedValue({
      label: 'Missed two',
      tone: 'caution',
      rationale: 'Two threshold sessions went unridden.',
    })

    const badge = await refreshTrainingStatus('tok-123')

    expect(badge?.tone).toBe('caution')
  })
})

describe('fetchNextRideRecommendation', () => {
  it('maps the response and includes the activity id when provided', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Go easy',
      next_session_recommendation: 'Recovery spin',
      recommendation_type: 'recovery',
    })

    const result = await fetchNextRideRecommendation('tok-123', 555)

    expect(result).toEqual({
      response: 'Go easy',
      nextSessionRecommendation: 'Recovery spin',
      recommendationType: 'recovery',
      planUpdates: undefined,
    })
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/next-ride-recommendation', {
      token: 'tok-123',
      method: 'POST',
      body: { stravaActivityId: 555 },
    })
  })

  it('omits the activity id and defaults the type when not provided', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Stick to the plan',
      next_session_recommendation: 'Planned intervals',
      recommendation_type: undefined,
    })

    const result = await fetchNextRideRecommendation('tok-123')

    expect(result.recommendationType).toBe('keep_as_planned')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/next-ride-recommendation', {
      token: 'tok-123',
      method: 'POST',
      body: {},
    })
  })
})

describe('processPendingFeedbacks', () => {
  it('POSTs the activity ids and returns the login summary', async () => {
    mockApiFetch.mockResolvedValue({ loginSummary: 'Summary updated' })

    const summary = await processPendingFeedbacks('tok-123', [1, 'abc'])

    expect(summary).toBe('Summary updated')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/process-pending-feedbacks', {
      token: 'tok-123',
      method: 'POST',
      body: { activityIds: [1, 'abc'] },
    })
  })

  it('returns an empty string when no summary is returned', async () => {
    mockApiFetch.mockResolvedValue({})
    expect(await processPendingFeedbacks('tok-123', [1])).toBe('')
  })
})
