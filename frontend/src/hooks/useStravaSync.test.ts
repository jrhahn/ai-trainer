import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { useStravaSync } from './useStravaSync'

const {
  mockGetStravaActivities,
  mockGetNewStravaActivities,
  mockGetIntervalsActivities,
  mockGetNewIntervalsActivities,
  mockAnalyseStravaActivities,
  mockGenerateTrainingPlan,
  mockRefreshLoginSummary,
  mockFetchTrainingPlan,
  mockUpdateCurrentUser,
  mockFetchCurrentUser,
  mockFetchMetricsHistory,
  mockFetchRideMetricsHistory,
  mockRecalculateMetrics,
} = vi.hoisted(() => ({
  mockGetStravaActivities: vi.fn(),
  mockGetNewStravaActivities: vi.fn(),
  mockGetIntervalsActivities: vi.fn(),
  mockGetNewIntervalsActivities: vi.fn(),
  mockAnalyseStravaActivities: vi.fn(),
  mockGenerateTrainingPlan: vi.fn(),
  mockRefreshLoginSummary: vi.fn(),
  mockFetchTrainingPlan: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
  mockFetchCurrentUser: vi.fn(),
  mockFetchMetricsHistory: vi.fn(),
  mockFetchRideMetricsHistory: vi.fn(),
  mockRecalculateMetrics: vi.fn(),
}))

vi.mock('../services/strava', () => ({
  getStravaActivities: mockGetStravaActivities,
  getNewStravaActivities: mockGetNewStravaActivities,
}))

vi.mock('../services/intervals', () => ({
  getIntervalsActivities: mockGetIntervalsActivities,
  getNewIntervalsActivities: mockGetNewIntervalsActivities,
}))

vi.mock('../services/ai', () => ({
  analyseStravaActivities: mockAnalyseStravaActivities,
  generateTrainingPlan: mockGenerateTrainingPlan,
  refreshLoginSummary: mockRefreshLoginSummary,
}))

vi.mock('../services/user', () => ({
  fetchTrainingPlan: mockFetchTrainingPlan,
  updateCurrentUser: mockUpdateCurrentUser,
  fetchCurrentUser: mockFetchCurrentUser,
  fetchMetricsHistory: mockFetchMetricsHistory,
  fetchRideMetricsHistory: mockFetchRideMetricsHistory,
  recalculateMetrics: mockRecalculateMetrics,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'general_fitness',
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

const mockActivities = [
  {
    id: 100,
    name: 'Morning Ride',
    type: 'Ride',
    distance: 30000,
    moving_time: 3600,
    start_date: '2025-01-10T08:00:00Z',
    average_watts: 200,
  },
]

const mockAssessment = {
  riderType: 'endurance' as const,
  notes: 'Good aerobic base.',
  rideInsights: 'Consistent power output.',
  lastRideFeedback: 'Strong finish.',
  loginSummary: 'Fresh summary from activity analysis.\n- Load: New ride is included.',
  hrZones: null,
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue(undefined)
  mockFetchTrainingPlan.mockResolvedValue([])
  mockGenerateTrainingPlan.mockResolvedValue([])
  mockRefreshLoginSummary.mockResolvedValue('')
  mockGetStravaActivities.mockResolvedValue([])
  mockGetNewStravaActivities.mockResolvedValue([])
  mockGetIntervalsActivities.mockResolvedValue([])
  mockGetNewIntervalsActivities.mockResolvedValue([])
  mockAnalyseStravaActivities.mockResolvedValue({ assessment: mockAssessment, planUpdates: undefined })
  mockRecalculateMetrics.mockResolvedValue({ updated: 1, ftpUsed: 250 })
  mockFetchCurrentUser.mockResolvedValue({
    profile: baseProfile,
    riderAssessment: {
      riderType: 'allrounder',
      notes: 'Older cached assessment',
      loginSummary: 'Stale summary before background analysis.\n- Load: Old ride only.',
    },
  })
  mockFetchMetricsHistory.mockResolvedValue([])
  mockFetchRideMetricsHistory.mockResolvedValue([])
})

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('useStravaSync', () => {
  it('returns idle status when there is no Strava connection', () => {
    useAppStore.setState({ authToken: 'tok', userProfile: baseProfile, stravaConnection: null })
    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(result.current.stravaActivities).toEqual([])
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
  })

  it('returns idle status when there is no auth token', () => {
    useAppStore.setState({ authToken: null, userProfile: baseProfile, stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' } })
    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
  })

  it('fetches Strava activities when connected and authenticated', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetStravaActivities).toHaveBeenCalledWith('tok')
    })
  })

  it('does not fetch Strava activities when automatic sync is disabled', () => {
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      lastStravaActivityId: 100,
      stravaAutoSyncEnabled: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
    expect(mockGetNewStravaActivities).not.toHaveBeenCalled()
  })

  it('runs analysis when activities are fetched and analysis has not been done yet', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith(
        mockActivities,
        'tok',
        undefined,
        undefined,
        'strava'
      )
      expect(result.current.analysisStatus).toBe('done')
    })
  })

  it('re-fetches the authoritative plan on incremental sync instead of persisting the local snapshot (#399)', async () => {
    const freshPlan = [
      {
        date: '2026-07-08',
        workoutType: 'intervals',
        title: 'VO2 Max Intervals',
        description: 'Hard intervals',
        durationMinutes: 60,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockGetNewStravaActivities.mockResolvedValue([
      {
        id: 201,
        name: 'New Ride',
        type: 'Ride',
        distance: 20000,
        moving_time: 3000,
        start_date: '2025-01-11T08:00:00Z',
        average_watts: 210,
      },
    ])
    // Backend returns targeted plan updates (already persisted server-side under
    // source="ride_review"). The hook must NOT PUT its stale in-memory plan back.
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: mockAssessment,
      planUpdates: [{ date: '2026-07-08', workoutType: 'recovery', title: 'Active Recovery' }],
    })
    mockFetchTrainingPlan.mockResolvedValue(freshPlan)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      lastStravaActivityId: 100,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetNewStravaActivities).toHaveBeenCalled()
      // Reconciles with the server's authoritative plan rather than the snapshot.
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
      expect(useAppStore.getState().trainingPlan).toEqual(freshPlan)
    })
  })

  it('does not regenerate the plan when an incremental sync yields no plan updates (#664)', async () => {
    const existingPlan = [
      {
        date: '2026-09-09',
        workoutType: 'strength',
        title: 'Core and Upper Body Strength',
        description: 'Keeping lower body load light ahead of Thursday.',
        durationMinutes: 45,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockGetNewStravaActivities.mockResolvedValue([
      {
        id: 201,
        name: 'New Ride',
        type: 'Ride',
        distance: 20000,
        moving_time: 3000,
        start_date: '2025-01-11T08:00:00Z',
        average_watts: 210,
      },
    ])
    // The ride needs no plan change. That is the *most* harmless outcome and
    // used to trigger a full regeneration, which rewrote days the coach had
    // just agreed with the athlete.
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: mockAssessment,
      planUpdates: [],
    })
    mockFetchTrainingPlan.mockResolvedValue(existingPlan)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      lastStravaActivityId: 100,
      trainingPlan: existingPlan,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
    })
    expect(mockGenerateTrainingPlan).not.toHaveBeenCalled()
    expect(useAppStore.getState().trainingPlan).toEqual(existingPlan)
  })

  it('does not regenerate an existing plan during a first-time analysis (#664)', async () => {
    const existingPlan = [
      {
        date: '2026-09-09',
        workoutType: 'strength',
        title: 'Core and Upper Body Strength',
        description: 'Agreed with the athlete in chat.',
        durationMinutes: 45,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockFetchTrainingPlan.mockResolvedValue(existingPlan)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
      trainingPlan: existingPlan,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockAnalyseStravaActivities).toHaveBeenCalled()
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
    })
    expect(mockGenerateTrainingPlan).not.toHaveBeenCalled()
  })

  it('still generates a plan on first-time analysis when there is none yet', async () => {
    const generated = [
      {
        date: '2026-09-09',
        workoutType: 'endurance',
        title: 'Steady Aerobic Ride',
        description: 'First plan for this athlete.',
        durationMinutes: 90,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockGenerateTrainingPlan.mockResolvedValue(generated)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
      trainingPlan: [],
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGenerateTrainingPlan).toHaveBeenCalledWith('tok')
      expect(useAppStore.getState().trainingPlan).toEqual(generated)
    })
  })

  it('sets activities in state after fetching', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.stravaActivities).toEqual(mockActivities)
    })
  })

  it('sets analysisStatus to error when fetch fails', async () => {
    mockGetStravaActivities.mockRejectedValue(new Error('Network error'))
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.analysisStatus).toBe('error')
      expect(result.current.analysisError).toBe('Failed to fetch Strava activities')
    })
  })

  it('sets analysisStatus to error when analysis fails', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockRejectedValue(new Error('AI service unavailable'))
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.analysisStatus).toBe('error')
      expect(result.current.analysisError).toBe('AI service unavailable')
    })
  })

  it('updates the rider assessment and user profile in the store after analysis', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      const { riderAssessment, userProfile } = useAppStore.getState()
      expect(riderAssessment?.riderType).toBe('endurance')
      expect(userProfile?.currentFTP).toBe(baseProfile.currentFTP)
    })
  })

  it('applies maxHeartRate from analysis hrZones to the user profile', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: {
        ...mockAssessment,
        hrZones: {
          zone1: { low: 100, high: 130 },
          zone2: { low: 130, high: 150 },
          zone3: { low: 150, high: 165 },
          zone4: { low: 165, high: 178 },
          zone5: { low: 178, high: 192 },
        },
      },
      planUpdates: undefined,
    })
    // fetchCurrentUser (called by recalculateAll) must return the updated maxHR
    // to reflect what the backend would return after updateCurrentUser persisted it.
    mockFetchCurrentUser.mockResolvedValue({
      profile: { ...baseProfile, maxHeartRate: 192 },
      riderAssessment: mockAssessment,
    })
    useAppStore.setState({
      authToken: 'tok',
      userProfile: { ...baseProfile, maxHeartRate: 180 },
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      // zone5.high from analysis (192) must have been sent to the backend
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith(
        'tok',
        expect.objectContaining({ maxHeartRate: 192 })
      )
      // and must be reflected in the store after the full flow completes
      expect(useAppStore.getState().userProfile?.maxHeartRate).toBe(192)
    })
  })

  it('keeps existing maxHeartRate when analysis returns no hrZones', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: { ...mockAssessment, hrZones: null },
      planUpdates: undefined,
    })
    useAppStore.setState({
      authToken: 'tok',
      userProfile: { ...baseProfile, maxHeartRate: 185 },
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(useAppStore.getState().userProfile?.maxHeartRate).toBe(185)
    })
  })

  it('keeps the freshly analysed login summary after metrics recalculation reloads the user', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetchCurrentUser).toHaveBeenCalledWith('tok')
      expect(useAppStore.getState().riderAssessment?.loginSummary).toBe(
        mockAssessment.loginSummary
      )
    })
  })

  it('refreshes the login summary after analysis when the analysis response omits one', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: {
        ...mockAssessment,
        loginSummary: undefined,
      },
      planUpdates: undefined,
    })
    mockRefreshLoginSummary.mockResolvedValue(
      'Refreshed summary after background analysis.\n- Recent activity: New ride included.'
    )
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockRefreshLoginSummary).toHaveBeenCalledWith('tok')
      expect(useAppStore.getState().riderAssessment?.loginSummary).toBe(
        'Refreshed summary after background analysis.\n- Recent activity: New ride included.'
      )
    })
  })

  it('does not re-run analysis when stravaAnalysisComplete is already true', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetStravaActivities).toHaveBeenCalled()
    })
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
  })

  it('saves the lastStravaActivityId after analysis', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      const { lastStravaActivityId } = useAppStore.getState()
      expect(lastStravaActivityId).toBe(100)
    })
  })

  it('uses Intervals.icu as the active polling source when Strava is not connected', async () => {
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Intervals Rider' },
      intervalsAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetIntervalsActivities).toHaveBeenCalledWith('tok')
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith(
        mockActivities,
        'tok',
        undefined,
        undefined,
        'intervals'
      )
      expect(useAppStore.getState().lastIntervalsActivityId).toBe(100)
    })
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
    expect(mockUpdateCurrentUser).toHaveBeenCalledWith(
      'tok',
      expect.objectContaining({
        intervalsAnalysisComplete: true,
        lastIntervalsActivityId: 100,
      })
    )
  })

  it('does not fetch Intervals.icu activities when automatic sync is disabled', () => {
    useAppStore.setState({
      authToken: 'tok',
      userProfile: baseProfile,
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Intervals Rider' },
      intervalsAnalysisComplete: true,
      lastIntervalsActivityId: 100,
      intervalsAutoSyncEnabled: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(mockGetIntervalsActivities).not.toHaveBeenCalled()
    expect(mockGetNewIntervalsActivities).not.toHaveBeenCalled()
  })
})
