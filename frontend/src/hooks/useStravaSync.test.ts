import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { useStravaSync } from './useStravaSync'

const {
  mockGetStravaActivities,
  mockGetNewStravaActivities,
  mockAnalyseStravaActivities,
  mockGenerateTrainingPlan,
  mockSaveTrainingPlan,
  mockUpdateCurrentUser,
} = vi.hoisted(() => ({
  mockGetStravaActivities: vi.fn(),
  mockGetNewStravaActivities: vi.fn(),
  mockAnalyseStravaActivities: vi.fn(),
  mockGenerateTrainingPlan: vi.fn(),
  mockSaveTrainingPlan: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
}))

vi.mock('../services/strava', () => ({
  getStravaActivities: mockGetStravaActivities,
  getNewStravaActivities: mockGetNewStravaActivities,
}))

vi.mock('../services/ai', () => ({
  analyseStravaActivities: mockAnalyseStravaActivities,
  generateTrainingPlan: mockGenerateTrainingPlan,
}))

vi.mock('../services/user', () => ({
  saveTrainingPlan: mockSaveTrainingPlan,
  updateCurrentUser: mockUpdateCurrentUser,
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
  estimatedFTP: 260,
  notes: 'Good aerobic base.',
  rideInsights: 'Consistent power output.',
  lastRideFeedback: 'Strong finish.',
  hrZones: null,
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue(undefined)
  mockSaveTrainingPlan.mockResolvedValue([])
  mockGenerateTrainingPlan.mockResolvedValue([])
  mockGetStravaActivities.mockResolvedValue([])
  mockGetNewStravaActivities.mockResolvedValue([])
  mockAnalyseStravaActivities.mockResolvedValue({ assessment: mockAssessment, planUpdates: undefined })
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
      expect(mockAnalyseStravaActivities).toHaveBeenCalledWith(mockActivities, 'tok', undefined)
      expect(result.current.analysisStatus).toBe('done')
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
})
