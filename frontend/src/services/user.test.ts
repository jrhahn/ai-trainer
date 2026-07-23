import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ API_BASE: '/api/v1', apiFetch: mockApiFetch }))

import {
  fetchCurrentUser,
  updateCurrentUser,
  fetchTrainingPlan,
  fetchWorkoutLogs,
  saveTrainingPlan,
  saveWorkoutLog,
  fetchRaceEvents,
  createRaceEvent,
  updateRaceEventRemote,
  deleteRaceEventRemote,
  fetchChatHistory,
  clearChatHistoryRemote,
  fetchCoachMemory,
  saveCoachMemoryRemote,
  fetchAthleteMemoryFacts,
  updateAthleteMemoryFact,
  confirmAthleteMemoryFact,
  deleteAthleteMemoryFact,
  fetchAthleteOpenQuestions,
  updateAthleteOpenQuestion,
  answerAthleteOpenQuestion,
  dismissAthleteOpenQuestion,
  deleteAthleteOpenQuestion,
  fetchValidationExperiments,
  updateValidationExperiment,
  completeValidationExperiment,
  dismissValidationExperiment,
  deleteValidationExperiment,
  deleteCurrentUser,
  recalculateMetrics,
  estimateFTP,
  uploadFitFile,
  uploadFitFiles,
  saveChatMessage,
  fetchMemoryPrivacySettings,
  updateMemoryPrivacySettings,
  clearAllMemory,
  exportMemory,
  fetchAIKeyStatus,
  saveAIKey,
  deleteAIKey,
  testAIKey,
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  fetchPlanHistory,
  fetchPlanHistoryStats,
  fetchAthleteModel,
  saveAthleteModel,
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
  vi.stubGlobal('fetch', vi.fn())
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
      stravaAutoSyncEnabled: false,
      intervalsAutoSyncEnabled: false,
      bikeType: 'road',
      trainingGoal: 'general_fitness',
      weeklyHours: 10,
      followsTrainingPlan: true,
      fitnessLevel: 'intermediate',
      consumedTokens: 12345,
      aiProvider: 'openai',
      riderAssessment: null,
      stravaConnection: null,
    })

    const result = await fetchCurrentUser('tok-123')

    expect(result.profile.name).toBe('Alice')
    expect(result.profile.email).toBe('alice@example.com')
    expect(result.profile.bikeType).toBe('road')
    expect(result.profile.consumedTokens).toBe(12345)
    expect(result.isOnboarded).toBe(true)
    expect(result.aiProvider).toBe('openai')
    expect(result.riderAssessment).toBeNull()
    expect(result.stravaConnection).toBeNull()
    expect(result.stravaAutoSyncEnabled).toBe(false)
    expect(result.intervalsAutoSyncEnabled).toBe(false)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me', { token: 'tok-123' })
  })

  it('applies default values for optional fields', async () => {
    mockApiFetch.mockResolvedValue({
      id: 'user-2',
      email: 'bob@example.com',
      isOnboarded: false,
      stravaAnalysisComplete: false,
      followsTrainingPlan: false,
      consumedTokens: 0,
      aiProvider: 'gemini',
    })

    const result = await fetchCurrentUser('tok-456')

    expect(result.profile.name).toBe('')
    expect(result.profile.bikeType).toBe('road')
    expect(result.profile.trainingGoal).toBe('general_fitness')
    expect(result.profile.weeklyHours).toBe(8)
    expect(result.profile.consumedTokens).toBe(0)
    expect(result.profile.fitnessLevel).toBe('intermediate')
    expect(result.lastStravaActivityId).toBeNull()
    expect(result.stravaAutoSyncEnabled).toBe(true)
    expect(result.intervalsAutoSyncEnabled).toBe(true)
  })

  it('normalizes retired training goals to general fitness', async () => {
    const retiredGoal = 'ftp' + '_improvement'
    mockApiFetch.mockResolvedValue({
      id: 'user-3',
      email: 'legacy@example.com',
      isOnboarded: true,
      stravaAnalysisComplete: false,
      followsTrainingPlan: false,
      trainingGoal: retiredGoal,
      aiProvider: 'openai',
    })

    const result = await fetchCurrentUser('tok-legacy')

    expect(result.profile.trainingGoal).toBe('general_fitness')
  })
})

describe('updateCurrentUser', () => {
  it('sends the Strava auto-sync preference to the backend', async () => {
    mockApiFetch
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({
        id: 'user-1',
        email: 'alice@example.com',
        isOnboarded: true,
        stravaAnalysisComplete: true,
        stravaAutoSyncEnabled: false,
        intervalsAutoSyncEnabled: false,
        followsTrainingPlan: true,
        consumedTokens: 0,
        aiProvider: 'openai',
      })

    await updateCurrentUser('tok-123', { stravaAutoSyncEnabled: false, intervalsAutoSyncEnabled: false })

    expect(mockApiFetch).toHaveBeenNthCalledWith(1, '/users/me', {
      token: 'tok-123',
      method: 'PUT',
      body: expect.objectContaining({
        stravaAutoSyncEnabled: false,
        intervalsAutoSyncEnabled: false,
      }),
    })
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

describe('athlete model (#384)', () => {
  it('fetches the long-term athlete model', async () => {
    const model = {
      ftpWatts: 260,
      vo2max: 58,
      pacingQuality: 'even',
      recoveryAbility: '',
      thresholdDurability: 'holds 30 min',
      heatTolerance: '',
      preferredTrainingStyle: 'intervals',
      strengths: ['threshold'],
      weaknesses: [],
      riskFactors: [],
      summary: 'Durable rider.',
      confidence: 0.7,
      updatedAt: '2026-07-10T00:00:00Z',
    }
    mockApiFetch.mockResolvedValue(model)

    const result = await fetchAthleteModel('tok-123')

    expect(result.ftpWatts).toBe(260)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-model', {
      token: 'tok-123',
    })
  })

  it('PUTs athlete edits without coach-owned fields', async () => {
    const edit = {
      ftpWatts: 265,
      vo2max: null,
      pacingQuality: 'even',
      recoveryAbility: '',
      thresholdDurability: '',
      heatTolerance: '',
      preferredTrainingStyle: '',
      strengths: ['climbing'],
      weaknesses: [],
      riskFactors: [],
      summary: 'edited',
    }
    mockApiFetch.mockResolvedValue({ ...edit, confidence: 0.5, updatedAt: null })

    await saveAthleteModel('tok-123', edit)

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-model', {
      token: 'tok-123',
      method: 'PUT',
      body: edit,
    })
  })
})

describe('fetchPlanHistory', () => {
  it('returns the entries array', async () => {
    mockApiFetch.mockResolvedValue({
      entries: [{ id: 'h1', date: '2026-05-01', source: 'coach_chat', applied: true }],
      total: 1,
    })

    const result = await fetchPlanHistory('tok-123')

    expect(result).toHaveLength(1)
    expect(result[0].source).toBe('coach_chat')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/plan-history', { token: 'tok-123' })
  })

  it('appends an encoded date query when provided', async () => {
    mockApiFetch.mockResolvedValue({ entries: [], total: 0 })

    await fetchPlanHistory('tok-123', '2026-05-01')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/plan-history?date=2026-05-01', {
      token: 'tok-123',
    })
  })
})

describe('fetchPlanHistoryStats', () => {
  it('returns the stats object', async () => {
    mockApiFetch.mockResolvedValue({
      bySource: { coach_chat: 2 },
      appliedCount: 2,
      blockedCount: 1,
      mostChangedDates: [{ date: '2026-05-01', count: 2 }],
      total: 3,
    })

    const result = await fetchPlanHistoryStats('tok-123')

    expect(result.total).toBe(3)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/plan-history/stats', { token: 'tok-123' })
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

describe('race events', () => {
  it('fetches race events from the backend envelope', async () => {
    mockApiFetch.mockResolvedValue({
      events: [{ id: 'race-1', date: '2026-06-01', distanceKm: 120, elevationM: 1800 }],
    })

    const result = await fetchRaceEvents('tok-123')

    expect(result).toHaveLength(1)
    expect(result[0].distanceKm).toBe(120)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/race-events', { token: 'tok-123' })
  })

  it('creates a race event', async () => {
    const event = { id: 'race-1', date: '2026-06-01', startTime: null, distanceKm: 120, elevationM: 1800 }
    mockApiFetch.mockResolvedValue(event)

    const result = await createRaceEvent('tok-123', {
      date: '2026-06-01',
      startTime: null,
      distanceKm: 120,
      elevationM: 1800,
    })

    expect(result).toEqual(event)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/race-events', {
      token: 'tok-123',
      method: 'POST',
      body: {
        date: '2026-06-01',
        startTime: null,
        distanceKm: 120,
        elevationM: 1800,
      },
    })
  })

  it('updates and deletes a race event', async () => {
    mockApiFetch.mockResolvedValueOnce({
      id: 'race-1',
      date: '2026-06-02',
      startTime: '09:00',
      distanceKm: 130,
      elevationM: 2000,
    })
    mockApiFetch.mockResolvedValueOnce(undefined)

    await updateRaceEventRemote('tok-123', 'race-1', {
      date: '2026-06-02',
      startTime: '09:00',
      distanceKm: 130,
      elevationM: 2000,
    })
    await deleteRaceEventRemote('tok-123', 'race-1')

    expect(mockApiFetch).toHaveBeenNthCalledWith(1, '/users/me/race-events/race-1', {
      token: 'tok-123',
      method: 'PUT',
      body: {
        date: '2026-06-02',
        startTime: '09:00',
        distanceKm: 130,
        elevationM: 2000,
      },
    })
    expect(mockApiFetch).toHaveBeenNthCalledWith(2, '/users/me/race-events/race-1', {
      token: 'tok-123',
      method: 'DELETE',
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

describe('athlete memory facts', () => {
  it('fetches the list and returns the facts array', async () => {
    const fact = { id: 'f1', fact: 'Likes hills', category: 'preference' }
    mockApiFetch.mockResolvedValue({ facts: [fact] })

    const result = await fetchAthleteMemoryFacts('tok-123')

    expect(result).toEqual([fact])
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-memory-facts', {
      token: 'tok-123',
    })
  })

  it('patches a correction', async () => {
    mockApiFetch.mockResolvedValue({ id: 'f1', fact: 'Likes long climbs' })

    await updateAthleteMemoryFact('tok-123', 'f1', { fact: 'Likes long climbs' })

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-memory-facts/f1', {
      token: 'tok-123',
      method: 'PATCH',
      body: { fact: 'Likes long climbs' },
    })
  })

  it('confirms a fact by setting user_confirmed status', async () => {
    mockApiFetch.mockResolvedValue({ id: 'f1', status: 'user_confirmed' })

    await confirmAthleteMemoryFact('tok-123', 'f1')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-memory-facts/f1', {
      token: 'tok-123',
      method: 'PATCH',
      body: { status: 'user_confirmed' },
    })
  })

  it('deletes a fact', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await deleteAthleteMemoryFact('tok-123', 'f1')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/athlete-memory-facts/f1', {
      token: 'tok-123',
      method: 'DELETE',
    })
  })
})

describe('validation experiments', () => {
  it('fetches the list and returns the experiments array', async () => {
    const experiment = {
      id: 'exp-1',
      protocol: 'Perform a 30-minute threshold test.',
    }
    mockApiFetch.mockResolvedValue({ experiments: [experiment] })

    const result = await fetchValidationExperiments('tok-123')

    expect(result).toEqual([experiment])
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/validation-experiments', {
      token: 'tok-123',
    })
  })

  it('patches an edit', async () => {
    mockApiFetch.mockResolvedValue({ id: 'exp-1', protocol: 'Shorter recoveries' })

    await updateValidationExperiment('tok-123', 'exp-1', {
      protocol: 'Shorter recoveries',
    })

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/users/me/validation-experiments/exp-1',
      {
        token: 'tok-123',
        method: 'PATCH',
        body: { protocol: 'Shorter recoveries' },
      },
    )
  })

  it('completes an experiment by setting completed status', async () => {
    mockApiFetch.mockResolvedValue({ id: 'exp-1', status: 'completed' })

    await completeValidationExperiment('tok-123', 'exp-1')

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/users/me/validation-experiments/exp-1',
      {
        token: 'tok-123',
        method: 'PATCH',
        body: { status: 'completed' },
      },
    )
  })

  it('dismisses an experiment by setting dismissed status', async () => {
    mockApiFetch.mockResolvedValue({ id: 'exp-1', status: 'dismissed' })

    await dismissValidationExperiment('tok-123', 'exp-1')

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/users/me/validation-experiments/exp-1',
      {
        token: 'tok-123',
        method: 'PATCH',
        body: { status: 'dismissed' },
      },
    )
  })

  it('deletes an experiment', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await deleteValidationExperiment('tok-123', 'exp-1')

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/users/me/validation-experiments/exp-1',
      {
        token: 'tok-123',
        method: 'DELETE',
      },
    )
  })
})

describe('open questions (#385)', () => {
  it('fetches the list and returns the openQuestions array', async () => {
    const question = { id: 'oq-1', question: 'Is FTP underestimated?' }
    mockApiFetch.mockResolvedValue({ openQuestions: [question] })

    const result = await fetchAthleteOpenQuestions('tok-123')

    expect(result).toEqual([question])
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/open-questions', {
      token: 'tok-123',
    })
  })

  it('patches an edit', async () => {
    mockApiFetch.mockResolvedValue({ id: 'oq-1', needs: 'Threshold test' })

    await updateAthleteOpenQuestion('tok-123', 'oq-1', { needs: 'Threshold test' })

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/open-questions/oq-1', {
      token: 'tok-123',
      method: 'PATCH',
      body: { needs: 'Threshold test' },
    })
  })

  it('answers a question by setting answered status', async () => {
    mockApiFetch.mockResolvedValue({ id: 'oq-1', status: 'answered' })

    await answerAthleteOpenQuestion('tok-123', 'oq-1')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/open-questions/oq-1', {
      token: 'tok-123',
      method: 'PATCH',
      body: { status: 'answered' },
    })
  })

  it('dismisses a question by setting dismissed status', async () => {
    mockApiFetch.mockResolvedValue({ id: 'oq-1', status: 'dismissed' })

    await dismissAthleteOpenQuestion('tok-123', 'oq-1')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/open-questions/oq-1', {
      token: 'tok-123',
      method: 'PATCH',
      body: { status: 'dismissed' },
    })
  })

  it('deletes a question', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await deleteAthleteOpenQuestion('tok-123', 'oq-1')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/open-questions/oq-1', {
      token: 'tok-123',
      method: 'DELETE',
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

describe('fit uploads', () => {
  it('normalizes the legacy single-file upload response', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ok',
        activity_id: 'activity-1',
        sport_type: 'cycling',
        duration_minutes: 75,
        average_power: 215,
        average_heart_rate: 151,
      }),
    } as Response)

    const result = await uploadFitFile(
      'tok-fit',
      new File(['fit'], 'ride.fit', { type: 'application/octet-stream' }),
    )

    expect(result).toEqual({
      status: 'ok',
      activityId: 'activity-1',
      sportType: 'cycling',
      durationMinutes: 75,
      averagePower: 215,
      averageHeartRate: 151,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/users/me/upload-fit', {
      method: 'POST',
      headers: { Authorization: 'Bearer tok-fit' },
      body: expect.any(FormData),
    })
  })

  it('posts multiple files to the bulk upload endpoint', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'partial',
        total: 2,
        imported: 1,
        skipped: 1,
        failed: 0,
        files: [
          { filename: 'one.fit', status: 'imported', message: 'Imported FIT activity' },
          { filename: 'two.fit', status: 'skipped', message: 'Already imported' },
        ],
      }),
    } as Response)

    const result = await uploadFitFiles('tok-fit', [
      new File(['one'], 'one.fit'),
      new File(['two'], 'two.fit'),
    ])

    expect(result.imported).toBe(1)
    expect(result.skipped).toBe(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/users/me/upload-fit/bulk', {
      method: 'POST',
      headers: { Authorization: 'Bearer tok-fit' },
      body: expect.any(FormData),
    })
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect((request.body as FormData).getAll('files')).toHaveLength(2)
  })
})

describe('setRideLegs', () => {
  it('patches the ride-feedback endpoint with the legs rating', async () => {
    mockApiFetch.mockResolvedValue({
      stravaActivityId: 9001,
      ride: { stravaActivityId: 9001, feelLegs: 'heavy' },
    })

    const { setRideLegs } = await import('./user')
    const result = await setRideLegs('tok-abc', 9001, 'heavy')

    expect(result.stravaActivityId).toBe(9001)
    expect(result.ride?.feelLegs).toBe('heavy')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ride-feedback/9001', {
      token: 'tok-abc',
      method: 'PATCH',
      body: { legs: 'heavy', externalActivityId: null },
    })
  })

  it('sends null to clear the legs rating', async () => {
    mockApiFetch.mockResolvedValue({
      stravaActivityId: 9002,
      ride: { stravaActivityId: 9002, feelLegs: null },
    })

    const { setRideLegs } = await import('./user')
    await setRideLegs('tok-abc', 9002, null)

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ride-feedback/9002', {
      token: 'tok-abc',
      method: 'PATCH',
      body: { legs: null, externalActivityId: null },
    })
  })

  it('sends the precision-safe external id for intervals rides (#441)', async () => {
    mockApiFetch.mockResolvedValue({
      stravaActivityId: 9003,
      ride: { stravaActivityId: 9003, feelLegs: 'fresh' },
    })

    const { setRideLegs } = await import('./user')
    await setRideLegs('tok-abc', 9003, 'fresh', 'i84213307')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ride-feedback/9003', {
      token: 'tok-abc',
      method: 'PATCH',
      body: { legs: 'fresh', externalActivityId: 'i84213307' },
    })
  })
})

describe('saveChatMessage', () => {
  it('POSTs the chat message', async () => {
    const msg = { role: 'user' as const, content: 'hi', timestamp: '2024-05-01T00:00:00Z' }
    mockApiFetch.mockResolvedValue({ ...msg, id: 'm1' })

    await saveChatMessage('tok', msg)

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/chat', {
      token: 'tok',
      method: 'POST',
      body: msg,
    })
  })
})

describe('memory privacy settings', () => {
  it('fetches the settings', async () => {
    mockApiFetch.mockResolvedValue({ memoryUpdatesEnabled: true })
    const result = await fetchMemoryPrivacySettings('tok')
    expect(result).toEqual({ memoryUpdatesEnabled: true })
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/memory-privacy', { token: 'tok' })
  })

  it('updates the settings via PUT', async () => {
    mockApiFetch.mockResolvedValue({ memoryUpdatesEnabled: false })
    const result = await updateMemoryPrivacySettings('tok', { memoryUpdatesEnabled: false })
    expect(result.memoryUpdatesEnabled).toBe(false)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/memory-privacy', {
      token: 'tok',
      method: 'PUT',
      body: { memoryUpdatesEnabled: false },
    })
  })
})

describe('clearAllMemory / exportMemory', () => {
  it('DELETEs all memory', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    await clearAllMemory('tok')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/memory', { token: 'tok', method: 'DELETE' })
  })

  it('exports memory', async () => {
    mockApiFetch.mockResolvedValue({ facts: [] })
    const result = await exportMemory('tok')
    expect(result).toEqual({ facts: [] })
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/memory-export', { token: 'tok' })
  })
})

describe('AI key management', () => {
  it('fetches the key status', async () => {
    mockApiFetch.mockResolvedValue({ provider: 'openai', hasOpenaiKey: true, hasGeminiKey: false })
    const result = await fetchAIKeyStatus('tok')
    expect(result.hasOpenaiKey).toBe(true)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ai-key/status', { token: 'tok' })
  })

  it('saves a key via PUT', async () => {
    mockApiFetch.mockResolvedValue({ provider: 'openai', hasOpenaiKey: true, hasGeminiKey: false })
    await saveAIKey('tok', 'openai', 'sk-123')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ai-key', {
      token: 'tok',
      method: 'PUT',
      body: { provider: 'openai', apiKey: 'sk-123' },
    })
  })

  it('deletes a key with the provider query param', async () => {
    mockApiFetch.mockResolvedValue(undefined)
    await deleteAIKey('tok', 'gemini')
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ai-key?provider=gemini', {
      token: 'tok',
      method: 'DELETE',
    })
  })

  it('tests a key via POST', async () => {
    mockApiFetch.mockResolvedValue({ ok: true })
    const result = await testAIKey('tok', 'openai', 'sk-123')
    expect(result.ok).toBe(true)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ai-key/test', {
      token: 'tok',
      method: 'POST',
      body: { provider: 'openai', apiKey: 'sk-123' },
    })
  })
})

describe('metrics history', () => {
  it('unwraps the snapshots array', async () => {
    mockApiFetch.mockResolvedValue({ snapshots: [{ recordedAt: '2024-05-01', source: 's' }] })
    const result = await fetchMetricsHistory('tok')
    expect(result).toHaveLength(1)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/metrics-history', { token: 'tok' })
  })

  it('unwraps the rides array', async () => {
    mockApiFetch.mockResolvedValue({ rides: [{ stravaActivityId: 1, activityDate: '2024-05-01', sportType: 'cycling' }] })
    const result = await fetchRideMetricsHistory('tok')
    expect(result).toHaveLength(1)
    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/ride-metrics-history', { token: 'tok' })
  })
})
