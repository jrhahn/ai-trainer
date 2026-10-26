import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { syncErrorMessage, useStravaSync } from './useStravaSync'
import { getPlan, setPlan } from '../queries/plan'
import { getProfile, setProfile } from '../queries/profile'

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

const mockGetIntervalsImportProgress = vi.hoisted(() => vi.fn())

vi.mock('../services/intervals', () => ({
  getIntervalsActivities: mockGetIntervalsActivities,
  getNewIntervalsActivities: mockGetNewIntervalsActivities,
  getIntervalsImportProgress: mockGetIntervalsImportProgress,
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

const IMPORT = { status: 'idle', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' }

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockGetIntervalsImportProgress.mockResolvedValue(IMPORT)
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
    setProfile(baseProfile)
    useAppStore.setState({ authToken: 'tok', stravaConnection: null })
    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(result.current.stravaActivities).toEqual([])
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
  })

  it('returns idle status when there is no auth token', () => {
    setProfile(baseProfile)
    useAppStore.setState({ authToken: null, stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' } })
    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    expect(result.current.analysisStatus).toBe('idle')
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
  })

  it('fetches Strava activities when connected and authenticated', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetStravaActivities).toHaveBeenCalledWith('tok')
    })
  })

  it('does not fetch Strava activities when automatic sync is disabled', () => {
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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

  it('reads the FTP at write time, so a save during the sync survives (ops#130)', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    // The athlete saves FTP 250 in Settings while the analysis is running.
    mockAnalyseStravaActivities.mockImplementation(async () => {
      const p = getProfile()!
      setProfile({ ...p, currentFTP: 250 })
      return { assessment: mockAssessment, planUpdates: undefined }
    })
    setProfile({ ...baseProfile, currentFTP: 200 })
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalled())

    // Never sends currentFTP, so the server keeps the saved value.
    expect(mockUpdateCurrentUser.mock.calls[0][1]).not.toHaveProperty('currentFTP')
    // History is recomputed with the FTP read at write time (250), not the 200
    // start-of-sync snapshot — the store-side half of the revert.
    await waitFor(() => expect(mockRecalculateMetrics).toHaveBeenCalledWith('tok', 250))
  })

  it('writes nothing back if the athlete signs out during the sync (ops#130)', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    // Sign-out (resetAll nulls the profile) while the analysis is running.
    mockAnalyseStravaActivities.mockImplementation(async () => {
      setProfile(null)
      return { assessment: mockAssessment, planUpdates: undefined }
    })
    setProfile({ ...baseProfile, currentFTP: 200 })
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(mockAnalyseStravaActivities).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))

    // The start-of-sync snapshot is not written back, so the signed-out
    // account's profile is not resurrected.
    expect(getProfile()).toBeNull()
    expect(mockUpdateCurrentUser).not.toHaveBeenCalled()
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      lastStravaActivityId: 100,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGetNewStravaActivities).toHaveBeenCalled()
      // Reconciles with the server's authoritative plan rather than the snapshot.
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
      expect(getPlan()).toEqual(freshPlan)
    })
  })

  it('does not regenerate the plan when an incremental sync yields no plan updates (#664)', async () => {
    const existingPlan = [
      {
        date: '2026-09-09',
        workoutType: 'strength' as const,
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
    setPlan(existingPlan)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      lastStravaActivityId: 100,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
    })
    expect(mockGenerateTrainingPlan).not.toHaveBeenCalled()
    expect(getPlan()).toEqual(existingPlan)
  })

  it('does not regenerate an existing plan during a first-time analysis (#664)', async () => {
    const existingPlan = [
      {
        date: '2026-09-09',
        workoutType: 'strength' as const,
        title: 'Core and Upper Body Strength',
        description: 'Agreed with the athlete in chat.',
        durationMinutes: 45,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockFetchTrainingPlan.mockResolvedValue(existingPlan)
    setPlan(existingPlan)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
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
        workoutType: 'endurance' as const,
        title: 'Steady Aerobic Ride',
        description: 'First plan for this athlete.',
        durationMinutes: 90,
      },
    ]
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockGenerateTrainingPlan.mockResolvedValue(generated)
    setPlan([])
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockGenerateTrainingPlan).toHaveBeenCalledWith('tok')
      expect(getPlan()).toEqual(generated)
    })
  })

  it('sets activities in state after fetching', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.analysisStatus).toBe('error')
      expect(result.current.analysisError).toBe(
        'Could not read your activities from Strava just now. Reload the page to try again.'
      )
    })
  })

  it('sets analysisStatus to error when analysis fails', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockRejectedValue(new Error('AI service unavailable'))
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      const { riderAssessment } = useAppStore.getState()
      expect(riderAssessment?.riderType).toBe('endurance')
      expect(getProfile()?.currentFTP).toBe(baseProfile.currentFTP)
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
    setProfile({ ...baseProfile, maxHeartRate: 180 })
    useAppStore.setState({
      authToken: 'tok',
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
      expect(getProfile()?.maxHeartRate).toBe(192)
    })
  })

  it('keeps existing maxHeartRate when analysis returns no hrZones', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: { ...mockAssessment, hrZones: null },
      planUpdates: undefined,
    })
    setProfile({ ...baseProfile, maxHeartRate: 185 })
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(getProfile()?.maxHeartRate).toBe(185)
    })
  })

  it('keeps the freshly analysed login summary after metrics recalculation reloads the user', async () => {
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      stravaAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      const { lastStravaActivityId } = useAppStore.getState()
      expect(lastStravaActivityId).toBe(100)
    })
  })

  it('asks for the intervals.icu key again when the server says it was refused (ai-trainer-ops#95)', async () => {
    mockGetIntervalsActivities.mockRejectedValue(
      Object.assign(new Error('Intervals.icu rejected the API key'), { status: 403 })
    )
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Intervals Rider' },
      intervalsAnalysisComplete: false,
    })

    const { result } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.analysisStatus).toBe('error')
      expect(result.current.analysisError).toBe('intervals.icu refused access. Copy your API key again in Settings.')
    })
  })

  it('uses Intervals.icu as the active polling source when Strava is not connected', async () => {
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
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
  // ---------------------------------------------------------------------------
  // Source precedence (ai-trainer-ops#49, answering #39 Q5)
  // ---------------------------------------------------------------------------

  it('reads from intervals.icu when both sources are connected and syncing', async () => {
    // The whole change. An athlete with both connections was being read from
    // Strava, and nothing but the order of one expression decided it.
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      intervalsConnection: { athleteId: '42', athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      intervalsAnalysisComplete: true,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(mockGetIntervalsActivities).toHaveBeenCalledWith('tok'))
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
  })

  it('falls back to Strava when intervals.icu sync is off', async () => {
    // The fallback has to survive the flip, or athletes who turned intervals
    // sync off lose their data source entirely.
    mockGetStravaActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      intervalsConnection: { athleteId: '42', athleteName: 'Test Athlete' },
      intervalsAutoSyncEnabled: false,
      stravaAnalysisComplete: true,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(mockGetStravaActivities).toHaveBeenCalledWith('tok'))
    expect(mockGetIntervalsActivities).not.toHaveBeenCalled()
  })

  it('analyses the new source as intervals, not as strava', async () => {
    // The source argument decides which cursor and which completion flag the
    // backend writes. Reading intervals activities and labelling them strava
    // would advance the wrong cursor and re-import everything next time.
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      intervalsConnection: { athleteId: '42', athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      intervalsAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith(
        mockActivities,
        'tok',
        undefined,
        undefined,
        'intervals'
      )
    })
  })

  it('does not regenerate an existing plan when the switch triggers the first intervals analysis', async () => {
    // The acceptance criterion with teeth. Existing athletes with both
    // connections have intervalsAnalysisComplete=false, so the flip triggers one
    // analysis on intervals data — and that analysis must respect the #664
    // guard, or everyone with both sources gets their plan rewritten on the
    // first load after this ships.
    const existingPlan = [
      {
        date: '2026-09-09',
        workoutType: 'strength' as const,
        title: 'Core and Upper Body Strength',
        description: 'Agreed with the athlete in chat.',
        durationMinutes: 45,
      },
    ]
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    mockFetchTrainingPlan.mockResolvedValue(existingPlan)
    setPlan(existingPlan)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: { athleteId: 1, athleteName: 'Test Athlete' },
      intervalsConnection: { athleteId: '42', athleteName: 'Test Athlete' },
      stravaAnalysisComplete: true,
      intervalsAnalysisComplete: false,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockAnalyseStravaActivities).toHaveBeenCalled()
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok')
    })
    expect(mockGenerateTrainingPlan).not.toHaveBeenCalled()
  })
})

describe('syncErrorMessage', () => {
  const withStatus = (status: number) => Object.assign(new Error('x'), { status })

  it('asks for the intervals.icu key again when it is refused', () => {
    expect(syncErrorMessage('intervals', withStatus(403))).toBe(
      'intervals.icu refused access. Copy your API key again in Settings.'
    )
  })

  it('asks to reconnect Strava when access was revoked', () => {
    expect(syncErrorMessage('strava', withStatus(403))).toBe('Strava refused access. Connect Strava again in Settings.')
  })

  it('does not blame the key for an outage', () => {
    expect(syncErrorMessage('intervals', withStatus(502))).toBe(
      'Could not read your activities from intervals.icu just now. Reload the page to try again.'
    )
  })
})

describe('the first intervals.icu analysis and a connect import (ai-trainer-ops#306)', () => {
  function connected() {
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Intervals Rider' },
      intervalsAnalysisComplete: false,
    })
  }

  it('waits while an import the page knows of is still at work', async () => {
    connected()
    useAppStore.setState({ intervalsImport: { ...IMPORT, status: 'analysing', total: 5, processed: 5, imported: 5 } as never })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(mockGetIntervalsActivities).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(mockGetIntervalsImportProgress).not.toHaveBeenCalled()
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
  })

  it('asks once, and leaves the rides to an import still at work', async () => {
    connected()
    const running = { ...IMPORT, status: 'running', total: 5, processed: 1 }
    mockGetIntervalsImportProgress.mockResolvedValue(running)

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(useAppStore.getState().intervalsImport).toEqual(running))
    expect(mockGetIntervalsImportProgress).toHaveBeenCalledWith('tok')
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
  })

  it('analyses nothing again after the coach looked at the import', async () => {
    connected()
    mockGetIntervalsImportProgress.mockResolvedValue({ ...IMPORT, status: 'done', imported: 5, analysed: true, planChanges: 0 })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() => expect(useAppStore.getState().intervalsAnalysisComplete).toBe(true))
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
  })

  it('analyses new rides the poll finds without waiting for an import', async () => {
    // Marked as handled once taken, so leaving them to an import would lose them.
    const newRide = { ...mockActivities[0], id: 101, name: 'Evening Ride' }
    mockGetIntervalsActivities.mockResolvedValue(mockActivities)
    mockGetNewIntervalsActivities.mockResolvedValue([newRide])
    setProfile(baseProfile)
    useAppStore.setState({
      authToken: 'tok',
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Intervals Rider' },
      intervalsAnalysisComplete: true,
      lastIntervalsActivityId: 100,
      intervalsImport: { ...IMPORT, status: 'running', total: 40 } as never,
    })

    renderHook(() => useStravaSync(), { wrapper: createWrapper() })

    await waitFor(() =>
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith([newRide], 'tok', undefined, undefined, 'intervals'),
    )
    expect(mockGetIntervalsImportProgress).not.toHaveBeenCalled()
  })

  it('analyses as before once an import ended without a look', async () => {
    connected()
    useAppStore.setState({ intervalsImport: { ...IMPORT, status: 'running', total: 2 } as never })
    const { result, rerender } = renderHook(() => useStravaSync(), { wrapper: createWrapper() })
    // The rides are in and the analysis has waited for the import once.
    await waitFor(() => expect(result.current.stravaActivities).toHaveLength(mockActivities.length))
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()

    // No plan yet, say, so the server did not look: the dashboard does.
    act(() => {
      useAppStore.setState({ intervalsImport: { ...IMPORT, status: 'done', total: 2, processed: 2, imported: 2, analysed: false } as never })
    })
    rerender()

    await waitFor(() =>
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith(mockActivities, 'tok', undefined, undefined, 'intervals'),
    )
  })
})
