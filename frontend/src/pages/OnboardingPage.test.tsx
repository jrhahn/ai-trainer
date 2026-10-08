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
  mockGetIntervalsActivities,
} = vi.hoisted(() => ({
  mockGenerateTrainingPlan: vi.fn(),
  mockAnalyseStravaActivities: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
  mockSaveTrainingPlan: vi.fn(),
  mockGetStravaActivities: vi.fn(),
  mockGetStravaAuthUrl: vi.fn(),
  mockDisconnectStrava: vi.fn(),
  mockGetIntervalsActivities: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  generateTrainingPlan: mockGenerateTrainingPlan,
  analyseStravaActivities: mockAnalyseStravaActivities,
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  saveTrainingPlan: mockSaveTrainingPlan,
}))

vi.mock('../services/intervals', () => ({
  getIntervalsActivities: mockGetIntervalsActivities,
  saveIntervalsConnection: vi.fn(),
  disconnectIntervals: vi.fn(),
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
  mockGetIntervalsActivities.mockResolvedValue([])
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

  it('persists FTP and max heart rate entered during manual onboarding', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await user.type(screen.getByPlaceholderText('e.g. 250'), '275')
    await user.type(screen.getByPlaceholderText('e.g. 185'), '189')

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1)
    })

    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      currentFTP: 275,
      maxHeartRate: 189,
      isOnboarded: true,
    })
    expect(useAppStore.getState().userProfile?.currentFTP).toBe(275)
    expect(useAppStore.getState().userProfile?.maxHeartRate).toBe(189)
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
    expect(screen.getByText(/Max Heart Rate \(bpm\)/i)).toBeInTheDocument()
  })

  it('only offers race prep and general fitness goals', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByRole('button', { name: /Race Prep/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /General Fitness/i })).toBeInTheDocument()
    expect(screen.queryByText(/FTP Improvement/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Weight Loss/i)).not.toBeInTheDocument()
  })

  it('clears race fields when general fitness is selected', async () => {
    setupStore({
      stravaConnection: null,
      userProfile: {
        ...baseProfile,
        trainingGoal: 'race',
        raceDate: '2026-09-15',
        raceDescription: 'Local gran fondo',
      },
    })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /General Fitness/i }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1)
    })

    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      trainingGoal: 'general_fitness',
      raceDate: undefined,
      raceDescription: undefined,
    })
  })

  it('analyses only the last 7 Strava activities before generating the plan', async () => {
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

  it('saves Strava onboarding progress immediately before leaving for OAuth', async () => {
    let capturedHref = ''
    Object.defineProperty(window, 'location', {
      value: {
        ...window.location,
        set href(url: string) {
          capturedHref = url
        },
        get href() {
          return capturedHref
        },
      },
      writable: true,
    })

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByText(/Connect with Strava/i))
    await user.click(screen.getByRole('button', { name: /connect strava/i }))

    await waitFor(() => expect(mockGetStravaAuthUrl).toHaveBeenCalled())
    const raw = sessionStorage.getItem('ai_trainer_onboarding_progress')
    expect(raw).not.toBeNull()
    const progress = JSON.parse(raw!)
    expect(progress.step).toBe(4)
    expect(progress.assessmentMethod).toBe('strava')
    expect(capturedHref).toBe('https://strava.test/auth')
  })

  it('does not send restingHeartRate during onboarding', async () => {
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
      expect(profileArg.restingHeartRate).toBeUndefined()
    })
  })

  // ---------------------------------------------------------------------------
  // intervals.icu first (ai-trainer-ops#20)
  // ---------------------------------------------------------------------------

  const intervalsRides = Array.from({ length: 9 }, (_, idx) => ({
    id: 900 + idx,
    name: `Ride ${idx + 1}`,
    type: 'Ride',
    distance: 40000,
    moving_time: 3600,
    elapsed_time: 3700,
    total_elevation_gain: 400,
    start_date: `2026-04-${String(10 - idx).padStart(2, '0')}T08:00:00Z`,
  }))

  const goToAssessment = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
  }

  it('offers intervals.icu before Strava on step 4', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    await goToAssessment(userEvent.setup())

    const intervals = screen.getByText('Connect intervals.icu')
    const strava = screen.getByText('Connect with Strava')
    expect(
      intervals.compareDocumentPosition(strava) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('shows the intervals.icu connection and waits for it before continuing', async () => {
    setupStore({ stravaConnection: null, intervalsConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)

    await user.click(screen.getByText('Connect intervals.icu'))

    expect(screen.getByText(/Connect intervals.icu to continue/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
  })

  it('preselects intervals.icu for an athlete who already connected it', async () => {
    setupStore({
      intervalsConnection: { athleteId: '0', athleteName: 'Alex' },
      userProfile: { ...baseProfile, currentFTP: undefined, maxHeartRate: undefined },
    })
    render(<OnboardingPage />)
    await goToAssessment(userEvent.setup())

    expect(screen.getByRole('button', { name: 'Analyse & Generate Plan' })).toBeEnabled()
  })

  it('analyses the last 7 intervals.icu rides without touching Strava', async () => {
    mockGetIntervalsActivities.mockResolvedValue(intervalsRides)
    mockAnalyseStravaActivities.mockResolvedValue({
      assessment: { estimatedFTP: 270, riderType: 'allrounder', notes: '' },
      planUpdates: [],
    })
    setupStore({
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Alex' },
      userProfile: { ...baseProfile, currentFTP: undefined, maxHeartRate: undefined },
    })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)

    await user.click(screen.getByRole('button', { name: 'Analyse & Generate Plan' }))

    await waitFor(() => {
      expect(mockGenerateTrainingPlan).toHaveBeenCalledTimes(1)
    })
    expect(mockGetStravaActivities).not.toHaveBeenCalled()
    expect(mockAnalyseStravaActivities.mock.calls[0][0]).toHaveLength(7)
    expect(mockAnalyseStravaActivities.mock.calls[0][4]).toBe('intervals')
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      isOnboarded: true,
      intervalsAnalysisComplete: true,
      stravaAnalysisComplete: false,
    })
    expect(useAppStore.getState().intervalsAnalysisComplete).toBe(true)
  })

  it('does not claim an analysis when intervals.icu has no rides yet', async () => {
    mockGetIntervalsActivities.mockResolvedValue([])
    setupStore({
      stravaConnection: null,
      intervalsConnection: { athleteId: '0', athleteName: 'Alex' },
      userProfile: { ...baseProfile, currentFTP: undefined, maxHeartRate: undefined },
    })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)

    await user.click(screen.getByRole('button', { name: 'Analyse & Generate Plan' }))

    await waitFor(() => {
      expect(mockGenerateTrainingPlan).toHaveBeenCalledTimes(1)
    })
    expect(mockAnalyseStravaActivities).not.toHaveBeenCalled()
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      intervalsAnalysisComplete: false,
    })
  })

  it('offers the analysis as soon as the intervals.icu connection arrives', async () => {
    setupStore({
      stravaConnection: null,
      intervalsConnection: null,
    })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)
    await user.click(screen.getByText('Connect intervals.icu'))
    // IntervalsConnect saves the key in-page and writes the connection to the
    // store; no redirect, so no saved progress to restore.
    useAppStore.setState({ intervalsConnection: { athleteId: '0', athleteName: 'Alex' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Analyse & Generate Plan' })).toBeEnabled()
    })
  })

  it('keeps the metrics the athlete entered ahead of a connected intervals.icu', async () => {
    // The same rule the Strava default always had: typed-in FTP or max HR is a
    // stated choice, a connection is only an offer.
    setupStore({
      intervalsConnection: { athleteId: '0', athleteName: 'Alex' },
      userProfile: { ...baseProfile, currentFTP: 250 },
    })
    render(<OnboardingPage />)
    await goToAssessment(userEvent.setup())

    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Analyse & Generate Plan' })).not.toBeInTheDocument()
  })

  it.each([
    ['intervals', 'intervals.icu (last 7 rides)'],
    ['strava', 'Strava (last 7 rides)'],
    ['manual', 'Manual'],
  ])('names the %s assessment in the summary after a reload', (method, label) => {
    sessionStorage.setItem(
      'ai_trainer_onboarding_progress',
      JSON.stringify({
        step: 5,
        trainingGoal: 'general_fitness',
        raceDate: '',
        raceDescription: '',
        assessmentMethod: method,
        followsTrainingPlan: false,
        fitnessLevel: 'intermediate',
      }),
    )
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)

    expect(screen.getByText(label)).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // The name given at registration survives onboarding (ai-trainer-ops#40)
  // ---------------------------------------------------------------------------

  it('never sends a name or email, even when the profile arrives after mount', async () => {
    // The original race: the form copied name and email at mount, before the
    // profile had loaded, and wrote the empty copy back on generate.
    setupStore({ stravaConnection: null, userProfile: null })
    render(<OnboardingPage />)
    useAppStore.setState({ userProfile: { ...baseProfile, name: 'Anna Test' } })
    const user = userEvent.setup()
    await goToAssessment(user)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1))
    const payload = mockUpdateCurrentUser.mock.calls[0][1]
    expect(payload.name).toBeUndefined()
    expect(payload.email).toBeUndefined()
    expect(useAppStore.getState().userProfile?.name).toBe('Anna Test')
  })

  it('shows the registered name and email in the summary', async () => {
    setupStore({ stravaConnection: null, userProfile: { ...baseProfile, name: 'Anna Test' } })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByText('Anna Test')).toBeInTheDocument()
    expect(screen.getByText('alex@example.com')).toBeInTheDocument()
  })

  it('keeps the name in the store when the profile arrives between render and click', async () => {
    setupStore({ stravaConnection: null, userProfile: { ...baseProfile, name: '' } })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    // The profile lands after the last render, just before the click.
    useAppStore.setState({ userProfile: { ...baseProfile, name: 'Anna Test' } })
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => expect(mockGenerateTrainingPlan).toHaveBeenCalled())
    expect(useAppStore.getState().userProfile?.name).toBe('Anna Test')
  })

  it('welcomes an athlete whose profile has not loaded yet', () => {
    setupStore({ stravaConnection: null, userProfile: null })
    render(<OnboardingPage />)
    expect(screen.getByText(/Welcome, athlete!/)).toBeInTheDocument()
  })

  it('still finishes onboarding without a loaded profile, sending no name', async () => {
    setupStore({ stravaConnection: null, userProfile: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await goToAssessment(user)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1))
    expect(mockUpdateCurrentUser.mock.calls[0][1].name).toBeUndefined()
  })
})
