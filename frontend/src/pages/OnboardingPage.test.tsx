import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OnboardingPage from './OnboardingPage'
import { useAppStore, type UserProfile } from '../store/useAppStore'

const {
  mockGenerateTrainingPlan,
  mockAnalyseStravaActivities,
  mockUpdateCurrentUser,
  mockSaveTrainingPlan,
  mockGetStravaActivities,
  mockGetStravaAuthUrl,
  mockDisconnectStrava,
} = vi.hoisted(() => ({
  mockGenerateTrainingPlan: vi.fn(),
  mockAnalyseStravaActivities: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
  mockSaveTrainingPlan: vi.fn(),
  mockGetStravaActivities: vi.fn(),
  mockGetStravaAuthUrl: vi.fn(),
  mockDisconnectStrava: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  generateTrainingPlan: mockGenerateTrainingPlan,
  analyseStravaActivities: mockAnalyseStravaActivities,
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  saveTrainingPlan: mockSaveTrainingPlan,
}))

vi.mock('../services/strava', () => ({
  getStravaActivities: mockGetStravaActivities,
  getStravaAuthUrl: mockGetStravaAuthUrl,
  disconnectStrava: mockDisconnectStrava,
}))

const baseProfile: UserProfile = {
  name: 'Alex',
  email: 'alex@example.com',
  bikeType: 'road',
  trainingGoal: 'general_fitness',
  weeklyHours: 8,
  followsTrainingPlan: false,
  fitnessLevel: 'intermediate',
}

function setupStore(overrides: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  useAppStore.setState({
    authToken: 'token-123',
    userProfile: baseProfile,
    ...overrides,
  })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue(undefined)
  mockSaveTrainingPlan.mockResolvedValue([])
  mockGenerateTrainingPlan.mockResolvedValue([])
  mockGetStravaActivities.mockResolvedValue([])
  mockGetStravaAuthUrl.mockResolvedValue('https://strava.test/auth')
  mockDisconnectStrava.mockResolvedValue(undefined)
})

describe('OnboardingPage', () => {
  it('shows manual fitness inputs when manual assessment is selected', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByText(/Current FTP \(watts\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Resting Heart Rate \(bpm\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Max Heart Rate \(bpm\)/i)).toBeInTheDocument()
  })

  it('analyses only the last 7 Strava rides before generating the plan', async () => {
    const activities = Array.from({ length: 10 }, (_, idx) => ({
      id: idx + 1,
      name: `Ride ${idx + 1}`,
      type: 'Ride',
      distance: 40000,
      moving_time: 3600,
      elapsed_time: 3700,
      total_elevation_gain: 400,
      start_date: `2026-04-${String(10 - idx).padStart(2, '0')}T08:00:00Z`,
      average_watts: 220,
      average_heartrate: 155,
    }))
    mockGetStravaActivities.mockResolvedValue(activities)
    mockAnalyseStravaActivities.mockResolvedValue({
      estimatedFTP: 280,
      estimatedThresholdHR: 170,
      riderType: 'allrounder',
      notes: 'Good sustained efforts.',
    })

    setupStore({
      stravaConnection: { athleteId: 42, athleteName: 'Alex Rider' },
      userProfile: {
        ...baseProfile,
        currentFTP: undefined,
        maxHeartRate: undefined,
      },
    })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      expect(mockAnalyseStravaActivities).toHaveBeenCalledTimes(1)
      expect(mockGenerateTrainingPlan).toHaveBeenCalledTimes(1)
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1)
    })

    expect(mockAnalyseStravaActivities.mock.calls[0][0]).toHaveLength(7)
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      currentFTP: 280,
      maxHeartRate: 195,
      isOnboarded: true,
      stravaAnalysisComplete: true,
    })
  })
})
