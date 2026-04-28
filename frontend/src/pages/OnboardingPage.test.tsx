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
  sessionStorage.clear()
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue(undefined)
  mockSaveTrainingPlan.mockResolvedValue([])
  mockGenerateTrainingPlan.mockResolvedValue([])
  mockGetStravaActivities.mockResolvedValue([])
  mockGetStravaAuthUrl.mockResolvedValue('https://strava.test/auth')
  mockDisconnectStrava.mockResolvedValue(undefined)
})

describe('OnboardingPage', () => {
  it('completes manual onboarding flow without Strava connection', async () => {
    const planDays = [
      {
        date: '2026-05-01',
        workoutType: 'endurance',
        title: 'Z2 Ride',
        description: 'Easy aerobic ride',
        durationMinutes: 90,
      },
    ]
    mockGenerateTrainingPlan.mockResolvedValue(planDays)
    mockSaveTrainingPlan.mockResolvedValue(planDays)

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    // Step 1 → 2 → 3 → 4 → 5
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      // Strava analysis must NOT run when there is no Strava connection
      expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
      // Profile update, plan generation and plan save must all run
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1)
      expect(mockGenerateTrainingPlan).toHaveBeenCalledTimes(1)
      expect(mockSaveTrainingPlan).toHaveBeenCalledTimes(1)
    })

    // updateCurrentUser must mark the user as onboarded with stravaAnalysisComplete=false
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      isOnboarded: true,
      stravaAnalysisComplete: false,
    })

    // saveTrainingPlan must receive the plan returned by generateTrainingPlan
    expect(mockSaveTrainingPlan.mock.calls[0][1]).toEqual(planDays)
  })

  it('shows error message when plan generation fails', async () => {
    mockGenerateTrainingPlan.mockRejectedValue(new Error('AI service unavailable'))

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      expect(screen.getByText('AI service unavailable')).toBeInTheDocument()
    })
  })

  it('shows fitness inputs on the Training Inputs step', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

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
      assessment: {
        estimatedFTP: 280,
        estimatedThresholdHR: 170,
        riderType: 'allrounder',
        notes: 'Good sustained efforts.',
        lastRideFeedback: 'Solid endurance ride. Keep it up!',
      },
      planUpdates: [],
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
    await user.click(screen.getByRole('button', { name: 'Analyse & Generate Plan' }))

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

  it('shows the Connect Strava option on step 4', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByText(/Connect with Strava/i)).toBeInTheDocument()
    expect(screen.getByText(/Use parameters I entered/i)).toBeInTheDocument()
  })

  it('shows the StravaConnect component when "Connect with Strava" is selected on step 4', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // Select "Connect with Strava"
    await user.click(screen.getByText(/Connect with Strava/i))

    // The StravaConnect "Connect Strava" button should now be visible
    expect(screen.getByRole('button', { name: /connect strava/i })).toBeInTheDocument()
  })

  it('disables Continue on step 4 when Strava method is selected but not yet connected', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // Select "Connect with Strava" option
    await user.click(screen.getByText(/Connect with Strava/i))

    // Continue button should be disabled until Strava is actually connected
    const continueButton = screen.getByRole('button', { name: 'Continue' })
    expect(continueButton).toBeDisabled()
  })

  it('enables Continue on step 4 once Strava is connected', async () => {
    // User already has Strava connected when they reach step 4
    setupStore({ stravaConnection: { athleteId: 42, athleteName: 'Alex Rider' } })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // With stravaConnection present, the default assessment method becomes 'strava'
    // and the button should be enabled (showing 'Analyse & Generate Plan')
    const actionButton = screen.getByRole('button', { name: 'Analyse & Generate Plan' })
    expect(actionButton).not.toBeDisabled()
  })

  it('saves onboarding progress to sessionStorage so it survives the OAuth redirect', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    // Advance to step 4 and select Strava method
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByText(/Connect with Strava/i))

    // sessionStorage must contain the current progress so it can be restored
    // after the OAuth redirect takes the user away and back to the onboarding page
    await waitFor(() => {
      const raw = sessionStorage.getItem('ai_trainer_onboarding_progress')
      expect(raw).not.toBeNull()
      const progress = JSON.parse(raw!)
      expect(progress.step).toBe(4)
      expect(progress.assessmentMethod).toBe('strava')
    })
  })

  it('uses age-based Max HR (220 − age) when no explicit Max HR is provided', async () => {
    mockGenerateTrainingPlan.mockResolvedValue([])
    mockSaveTrainingPlan.mockResolvedValue([])

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    // Navigate to step 3 (Training Inputs)
    await user.click(screen.getByRole('button', { name: 'Continue' })) // step 1→2
    await user.click(screen.getByRole('button', { name: 'Continue' })) // step 2→3 (Training Inputs)

    // Enter age 35 — expected estimated Max HR = 220 - 35 = 185
    const ageInputs = screen.getAllByPlaceholderText(/e\.g\. 35/)
    await user.type(ageInputs[0], '35')

    // Live preview should show estimated Max HR
    expect(screen.getByText(/Estimated Max HR: 185 bpm/i)).toBeInTheDocument()

    // Proceed through assessment step to step 5 and generate plan
    await user.click(screen.getByRole('button', { name: 'Continue' })) // step 3→4 (assessment, manual default)
    await user.click(screen.getByRole('button', { name: 'Continue' })) // step 4→5
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      const profileArg = mockUpdateCurrentUser.mock.calls[0][1]
      expect(profileArg.maxHeartRate).toBe(185)
    })
  })

  it('defaults resting HR to 60 when not provided during onboarding', async () => {
    mockGenerateTrainingPlan.mockResolvedValue([])
    mockSaveTrainingPlan.mockResolvedValue([])

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    // Navigate to step 5 without setting any HR values
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      const profileArg = mockUpdateCurrentUser.mock.calls[0][1]
      // Resting HR must default to 60 when left blank
      expect(profileArg.restingHeartRate).toBe(60)
    })
  })
})
