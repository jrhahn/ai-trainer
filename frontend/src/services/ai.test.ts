import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TrainingDay, UserProfile } from '../types/domain'

const mockApiFetch = vi.hoisted(() => vi.fn())

vi.mock('./api', () => ({
  apiFetch: mockApiFetch,
}))

import {
  analyseStravaActivities,
  askTrainer,
  extractAthleteFacts,
  fetchSeasonPlan,
  fetchPlanTranslations,
  writePlanTranslations,
  generateTrainingPlan,
  processPendingFeedbacks,
  rateCompletedWorkout,
  refreshAthleteModel,
  refreshLoginSummary,
  refreshTrainingStatus,
  resolveRideMatch,
  writeWeekGoals,
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
  it('calls the backend ask-trainer endpoint and returns its plan updates and plan', async () => {
    mockApiFetch.mockResolvedValue({
      response: 'Try interval training twice a week.',
      planUpdates: [
        { date: '2024-05-02', workoutType: 'rest', title: 'Rest', description: 'Rest up', durationMinutes: 0 },
      ],
      updatedPlan: [
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

  it('forwards the race offer, and none when there is none (ai-trainer-ops#175)', async () => {
    mockApiFetch.mockResolvedValue({ response: 'ok', raceOffer: { date: '2026-11-11' } })
    expect((await askTrainer('race in 3 weeks', 'token-123')).raceOffer).toEqual({ date: '2026-11-11' })

    mockApiFetch.mockResolvedValue({ response: 'ok' })
    expect((await askTrainer('hi', 'token-123')).raceOffer).toBeNull()
  })

  it('forwards the app\'s notes, and an empty list when there are none (ai-trainer-ops#262)', async () => {
    const notes = [{ kind: 'location', text: 'I set your usual training location to Freiburg.' }]
    mockApiFetch.mockResolvedValue({ response: 'ok', notes })
    expect((await askTrainer('I train near Freiburg', 'token-123')).notes).toEqual(notes)

    mockApiFetch.mockResolvedValue({ response: 'ok' })
    expect((await askTrainer('hi', 'token-123')).notes).toEqual([])
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
      objectiveRationale: 'keeps the last descent enjoyable',
      contextRationale: 'history of overreaching favours rest',
    })

    const result = await askTrainer('Can I ride today?', 'token-123')

    expect(result.physiologyRationale).toBe('fresh enough for an easy ride')
    expect(result.objectiveRationale).toBe('keeps the last descent enjoyable')
    expect(result.contextRationale).toBe('history of overreaching favours rest')
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

describe('season plan', () => {
  it('reads the weeks without asking for goals', async () => {
    mockApiFetch.mockResolvedValue({ weeks: [{ weekStart: '2026-10-12' }] })

    expect(await fetchSeasonPlan('token-123')).toEqual([{ weekStart: '2026-10-12' }])
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/season-plan', { token: 'token-123' })
  })

  it('asks for the missing goals with a POST', async () => {
    mockApiFetch.mockResolvedValue({ weeks: [] })

    expect(await writeWeekGoals('token-123')).toEqual([])
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/season-plan/goals', { token: 'token-123', method: 'POST' })
  })
})

describe('plan translations (ai-trainer-ops#121)', () => {
  it('reads the stored ones in the language asked for', async () => {
    const answer = { language: 'de', translations: { Rest: 'Ruhetag' }, missing: false }
    mockApiFetch.mockResolvedValue(answer)

    expect(await fetchPlanTranslations('token-123', 'de')).toEqual(answer)
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/plan-translations', {
      token: 'token-123',
      headers: { 'X-App-Language': 'de' },
    })
  })

  it('asks for the missing ones with a POST', async () => {
    mockApiFetch.mockResolvedValue({ language: 'de', translations: {}, missing: false })

    await writePlanTranslations('token-123', 'de')
    expect(mockApiFetch).toHaveBeenCalledWith('/ai/plan-translations', {
      token: 'token-123',
      method: 'POST',
      headers: { 'X-App-Language': 'de' },
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
      body: { plannedDate: '2026-05-06', stravaActivityId: 7001, externalActivityId: null },
    })
  })

  it('sends the chosen session on a two-a-day', async () => {
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 7002 } })

    await resolveRideMatch('token-123', '2026-05-06', 7002, 1)

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'token-123',
      method: 'POST',
      body: {
        plannedDate: '2026-05-06',
        stravaActivityId: 7002,
        externalActivityId: null,
        plannedSlot: 1,
      },
    })
  })

  it('sends slot 0 explicitly when it is asked to', async () => {
    // 0 is falsy, so the morning session has to survive the optional check.
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 7003 } })

    await resolveRideMatch('token-123', '2026-05-06', 7003, 0)

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'token-123',
      method: 'POST',
      body: {
        plannedDate: '2026-05-06',
        stravaActivityId: 7003,
        externalActivityId: null,
        plannedSlot: 0,
      },
    })
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

describe('resolveRideMatch', () => {
  it('sends the precision-safe external id alongside the numeric one (#441)', async () => {
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 1 } })

    const { resolveRideMatch } = await import('./ai')
    await resolveRideMatch('tok', '2026-08-09', Number('7846148097020609552'), 0, 'i174087702')

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'tok',
      method: 'POST',
      body: {
        plannedDate: '2026-08-09',
        // Already rounded by the time it is a JS number — which is the whole
        // reason the string id has to travel with it.
        stravaActivityId: Number('7846148097020609552'),
        externalActivityId: 'i174087702',
        plannedSlot: 0,
      },
    })
  })

  it('sends a null external id for a Strava ride rather than omitting it', async () => {
    mockApiFetch.mockResolvedValue({ ride: { stravaActivityId: 1 } })

    const { resolveRideMatch } = await import('./ai')
    await resolveRideMatch('tok', '2026-05-20', 64002)

    expect(mockApiFetch).toHaveBeenCalledWith('/ai/resolve-ride-match', {
      token: 'tok',
      method: 'POST',
      body: {
        plannedDate: '2026-05-20',
        stravaActivityId: 64002,
        externalActivityId: null,
      },
    })
  })
})
