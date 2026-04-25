import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { useMetricsPipeline } from './useMetricsPipeline'

const {
  mockFetchCurrentUser,
  mockFetchMetricsHistory,
  mockFetchRideMetricsHistory,
  mockRecalculateMetrics,
  mockUpdateCurrentUser,
} = vi.hoisted(() => ({
  mockFetchCurrentUser: vi.fn(),
  mockFetchMetricsHistory: vi.fn(),
  mockFetchRideMetricsHistory: vi.fn(),
  mockRecalculateMetrics: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchCurrentUser: mockFetchCurrentUser,
  fetchMetricsHistory: mockFetchMetricsHistory,
  fetchRideMetricsHistory: mockFetchRideMetricsHistory,
  recalculateMetrics: mockRecalculateMetrics,
  updateCurrentUser: mockUpdateCurrentUser,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
  currentFTP: 250,
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({
    authToken: 'tok-123',
    userProfile: baseProfile,
    riderAssessment: {
      riderType: 'allrounder',
      notes: 'Existing assessment',
      lastRideFeedback: 'Old feedback',
    },
  })
  vi.clearAllMocks()

  mockRecalculateMetrics.mockResolvedValue({ updated: 12, ftpUsed: 260 })
  mockFetchCurrentUser.mockResolvedValue({
    profile: { ...baseProfile, currentFTP: 260 },
    isOnboarded: true,
    stravaAnalysisComplete: true,
    lastStravaActivityId: 9001,
    aiProvider: 'openai',
    riderAssessment: {
      riderType: 'allrounder',
      notes: 'Existing assessment',
      lastRideFeedback: 'Updated after recalculation',
    },
    stravaConnection: null,
  })
  mockFetchMetricsHistory.mockResolvedValue([
    { recordedAt: '2026-04-01T09:00:00Z', ftp: 260, source: 'manual_recalculate' },
  ])
  mockFetchRideMetricsHistory.mockResolvedValue([
    { activityDate: '2026-04-01', sportType: 'cycling', tss: 88, ctlAfter: 40.1, atlAfter: 48.5, tsbAfter: -8.4 },
  ])
  mockUpdateCurrentUser.mockResolvedValue(undefined)
})

describe('useMetricsPipeline', () => {
  it('refreshes rider assessment after recalculation', async () => {
    const { result } = renderHook(() => useMetricsPipeline())

    await act(async () => {
      await result.current.recalculateAll(260)
    })

    await waitFor(() => {
      expect(mockFetchCurrentUser).toHaveBeenCalledWith('tok-123')
      expect(useAppStore.getState().riderAssessment?.lastRideFeedback).toBe(
        'Updated after recalculation'
      )
      expect(useAppStore.getState().userProfile?.currentFTP).toBe(260)
    })
  })
})