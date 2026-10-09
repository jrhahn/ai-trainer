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
  mockFetchAIKeyStatus,
  mockSaveAIKey,
  mockTestAIKey,
} = vi.hoisted(() => ({
  mockGenerateTrainingPlan: vi.fn(),
  mockAnalyseStravaActivities: vi.fn(),
  mockUpdateCurrentUser: vi.fn(),
  mockSaveTrainingPlan: vi.fn(),
  mockGetStravaActivities: vi.fn(),
  mockGetStravaAuthUrl: vi.fn(),
  mockDisconnectStrava: vi.fn(),
  mockGetIntervalsActivities: vi.fn(),
  mockFetchAIKeyStatus: vi.fn(),
  mockSaveAIKey: vi.fn(),
  mockTestAIKey: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  generateTrainingPlan: mockGenerateTrainingPlan,
  analyseStravaActivities: mockAnalyseStravaActivities,
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  saveTrainingPlan: mockSaveTrainingPlan,
  fetchAIKeyStatus: mockFetchAIKeyStatus,
  saveAIKey: mockSaveAIKey,
  testAIKey: mockTestAIKey,
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
  // A hosted deployment by default, so the tests written before the key step
  // keep the five-step flow they were written against (ai-trainer-ops#41).
  mockFetchAIKeyStatus.mockResolvedValue({
    provider: 'gemini',
    hasOpenaiKey: false,
    hasGeminiKey: false,
    keyRequired: false,
  })
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
      // Two updates now, not one: the profile, then `isOnboarded` once a plan
      // exists (ai-trainer-ops#41).
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2)
      expect(mockGenerateTrainingPlan).toHaveBeenCalledTimes(1)
      expect(mockSaveTrainingPlan).toHaveBeenCalledTimes(1)
    })

    // The profile goes first and must not claim onboarding yet.
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      stravaAnalysisComplete: false,
    })
    expect(mockUpdateCurrentUser.mock.calls[0][1].isOnboarded).toBeUndefined()
    expect(mockUpdateCurrentUser.mock.calls[1][1]).toEqual({ isOnboarded: true })

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
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2)
    })

    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      currentFTP: 275,
      maxHeartRate: 189,
    })
    expect(mockUpdateCurrentUser.mock.calls[1][1]).toEqual({ isOnboarded: true })
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

  it('does not mark the account onboarded when the plan cannot be generated', async () => {
    // The dead end from ai-trainer-ops#41. `isOnboarded: true` used to be sent
    // with the profile, before the plan was generated, so a 402 for a missing AI
    // key — the ordinary case in BYOK-only mode — left the account onboarded
    // with no plan. The error showed here and the local flag stayed false, so it
    // looked recoverable; a reload read `isOnboarded` from the server and landed
    // on the dashboard saying "No session planned for today", with nothing
    // anywhere naming the cause.
    mockGenerateTrainingPlan.mockRejectedValue(
      new Error('No gemini API key configured. Please add your key in Settings → AI Provider.'),
    )

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    // The athlete is now taken to the key step rather than left on the summary
    // with a message naming a Settings page they cannot reach — the other half
    // of ai-trainer-ops#41. The backend's own wording is replaced on the way,
    // because "add your key in Settings → AI Provider" read above the very form
    // asking for that key is worse than saying nothing.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /add your gemini key/i })).toBeInTheDocument()
    })
    expect(screen.getByText(/needs a model before it can write a plan/i)).toBeInTheDocument()
    expect(screen.queryByText(/add your key in Settings/)).not.toBeInTheDocument()

    // The profile was written, and that is fine — it is the athlete's own data.
    expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(1)
    // What must not have happened is the onboarding flag.
    for (const call of mockUpdateCurrentUser.mock.calls) {
      expect(call[1].isOnboarded).toBeUndefined()
    }
    // No plan was saved either, so server and client agree: not onboarded.
    expect(mockSaveTrainingPlan).not.toHaveBeenCalled()
    expect(useAppStore.getState().isOnboarded).toBe(false)
  })

  it('does not mark the account onboarded when saving the plan fails', async () => {
    // The same ordering question one step later: the plan generated but did not
    // persist, so the server still has no plan and must not say onboarded.
    mockSaveTrainingPlan.mockRejectedValue(new Error('Could not save the plan.'))

    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    await waitFor(() => {
      expect(screen.getByText('Could not save the plan.')).toBeInTheDocument()
    })

    for (const call of mockUpdateCurrentUser.mock.calls) {
      expect(call[1].isOnboarded).toBeUndefined()
    }
    expect(useAppStore.getState().isOnboarded).toBe(false)
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
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2)
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
      expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2)
    })

    expect(mockAnalyseStravaActivities.mock.calls[0][0]).toHaveLength(7)
    expect(mockUpdateCurrentUser.mock.calls[0][1]).toMatchObject({
      stravaAnalysisComplete: true,
    })
    expect(mockUpdateCurrentUser.mock.calls[1][1]).toEqual({ isOnboarded: true })
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
      intervalsAnalysisComplete: true,
      stravaAnalysisComplete: false,
    })
    expect(mockUpdateCurrentUser.mock.calls[1][1]).toEqual({ isOnboarded: true })
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

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2))
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

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalledTimes(2))
    expect(mockUpdateCurrentUser.mock.calls[0][1].name).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// The conditional key step (ai-trainer-ops#41)
// ---------------------------------------------------------------------------

/** Walk the four question steps, which is where the order diverges. */
async function walkTheQuestions(user: ReturnType<typeof userEvent.setup>) {
  for (let i = 0; i < 4; i++) {
    await user.click(screen.getByRole('button', { name: 'Continue' }))
  }
}

describe('the step order follows the deployment', () => {
  it('has five steps when the server provides the model', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)

    expect(await screen.findByText('Step 1 of 5')).toBeInTheDocument()
  })

  it('has six when the athlete has to bring a key', async () => {
    mockFetchAIKeyStatus.mockResolvedValue({ keyRequired: true })
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)

    expect(await screen.findByText('Step 1 of 6')).toBeInTheDocument()
  })

  it('never asks a hosted athlete for a key', async () => {
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 5')

    await walkTheQuestions(user)

    expect(screen.getByText('Ready to Go!')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /add your gemini key/i })).not.toBeInTheDocument()
  })

  it('puts the key step between the questions and the summary', async () => {
    // Before the summary, so "Generate" is never a button that cannot work;
    // after the questions, so nothing is asked for before the athlete knows
    // what they are getting.
    mockFetchAIKeyStatus.mockResolvedValue({ keyRequired: true })
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 6')

    await walkTheQuestions(user)

    expect(screen.getByRole('heading', { name: /add your gemini key/i })).toBeInTheDocument()
    expect(screen.queryByText('Ready to Go!')).not.toBeInTheDocument()
    // Fifth of six: the id is 6, the position is 5.
    expect(screen.getByText('Step 5 of 6')).toBeInTheDocument()
  })

  it('treats a backend that does not know the field as hosted', async () => {
    // An older backend. Asking for a key nobody needs is a worse failure than
    // the one this fixes.
    mockFetchAIKeyStatus.mockResolvedValue({
      provider: 'gemini',
      hasOpenaiKey: false,
      hasGeminiKey: false,
    })
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)

    expect(await screen.findByText('Step 1 of 5')).toBeInTheDocument()
  })

  it('carries on quietly when the status request fails outright', async () => {
    mockFetchAIKeyStatus.mockRejectedValue(new Error('network unreachable'))
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 5')

    await walkTheQuestions(user)

    // No banner on the welcome screen of a flow that still works, and the
    // generate path recovers on its own.
    expect(screen.getByText('Ready to Go!')).toBeInTheDocument()
    expect(screen.queryByText(/network unreachable/i)).not.toBeInTheDocument()
  })
})

describe('the key step', () => {
  async function reachKeyStep() {
    mockFetchAIKeyStatus.mockResolvedValue({ keyRequired: true })
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 6')
    await walkTheQuestions(user)
    await screen.findByRole('heading', { name: /add your gemini key/i })
    return user
  }

  it('links to the guide and offers both buttons', async () => {
    await reachKeyStep()

    const link = screen.getByRole('link', { name: /get a key/i })
    expect(link).toHaveAttribute('href', 'https://aistudio.google.com/apikey')
    // A new tab without handing over the opener.
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
    expect(screen.getByRole('button', { name: /test key/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save and continue/i })).toBeInTheDocument()
  })

  it('keeps both buttons out of reach until something is typed', async () => {
    await reachKeyStep()

    expect(screen.getByRole('button', { name: /test key/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /save and continue/i })).toBeDisabled()
  })

  it('does not show the key in plain text', async () => {
    const user = await reachKeyStep()
    const field = screen.getByLabelText(/gemini api key/i)

    await user.type(field, 'AIzaSecret')

    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('autocomplete', 'off')
  })

  it('says so when the key works, without storing it', async () => {
    const user = await reachKeyStep()
    mockTestAIKey.mockResolvedValue({ ok: true })

    await user.type(screen.getByLabelText(/gemini api key/i), 'AIzaGood')
    await user.click(screen.getByRole('button', { name: /test key/i }))

    expect(await screen.findByText('The key works.')).toBeInTheDocument()
    expect(mockTestAIKey).toHaveBeenCalledWith('token-123', 'gemini', 'AIzaGood')
    expect(mockSaveAIKey).not.toHaveBeenCalled()
    // Still here: a test is not a commitment.
    expect(screen.getByRole('heading', { name: /add your gemini key/i })).toBeInTheDocument()
  })

  it("shows the provider's own complaint about a bad key", async () => {
    const user = await reachKeyStep()
    mockTestAIKey.mockRejectedValue(new Error('Key validation failed.'))

    await user.type(screen.getByLabelText(/gemini api key/i), 'AIzaBad')
    await user.click(screen.getByRole('button', { name: /test key/i }))

    expect(await screen.findByText('Key validation failed.')).toBeInTheDocument()
    expect(screen.queryByText('The key works.')).not.toBeInTheDocument()
  })

  it('forgets that a key was tested once it is edited', async () => {
    // Otherwise "The key works." stays on screen above a different key.
    const user = await reachKeyStep()
    mockTestAIKey.mockResolvedValue({ ok: true })

    await user.type(screen.getByLabelText(/gemini api key/i), 'AIzaGood')
    await user.click(screen.getByRole('button', { name: /test key/i }))
    await screen.findByText('The key works.')

    await user.type(screen.getByLabelText(/gemini api key/i), 'X')

    expect(screen.queryByText('The key works.')).not.toBeInTheDocument()
  })

  it('saves the key and moves to the summary', async () => {
    const user = await reachKeyStep()
    mockSaveAIKey.mockResolvedValue({ keyRequired: false })

    await user.type(screen.getByLabelText(/gemini api key/i), 'AIzaGood')
    await user.click(screen.getByRole('button', { name: /save and continue/i }))

    expect(await screen.findByText('Ready to Go!')).toBeInTheDocument()
    expect(mockSaveAIKey).toHaveBeenCalledWith('token-123', 'gemini', 'AIzaGood')
    // And the count drops back, because nothing is required any more.
    expect(screen.getByText('Step 5 of 5')).toBeInTheDocument()
  })

  it('stays put when the backend stores the key and still cannot use it', async () => {
    // A 200 is not the answer. The endpoint recomputes `keyRequired`, and
    // advancing on the status code alone would put the athlete back on
    // "Generate" with the same 402 — the loop ai-trainer-ops#41 describes.
    const user = await reachKeyStep()
    mockSaveAIKey.mockResolvedValue({ keyRequired: true })

    await user.type(screen.getByLabelText(/gemini api key/i), 'AIzaWrongProvider')
    await user.click(screen.getByRole('button', { name: /save and continue/i }))

    expect(await screen.findByText(/still cannot reach a model with it/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /add your gemini key/i })).toBeInTheDocument()
    expect(screen.queryByText('Ready to Go!')).not.toBeInTheDocument()
  })

  it('does not let Continue walk past it', async () => {
    // The step advances through Save, which is what writes the key. A Continue
    // that moved on without saving would hand over the same 402 one screen on.
    await reachKeyStep()

    for (const button of screen.queryAllByRole('button', { name: 'Continue' })) {
      expect(button).toBeDisabled()
    }
  })

  it('does not offer "Analyse & Generate Plan" while a key is still missing', async () => {
    // A connected athlete generates straight from the assessment step. That
    // shortcut has to stop at the key step too, or it skips the one screen that
    // can fix the 402.
    mockFetchAIKeyStatus.mockResolvedValue({ keyRequired: true })
    setupStore({
      stravaConnection: { athleteId: 1, athleteName: 'Alex' },
    } as never)
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 6')

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(
      screen.queryByRole('button', { name: /analyse & generate plan/i })
    ).not.toBeInTheDocument()
    expect(mockGenerateTrainingPlan).not.toHaveBeenCalled()
  })
})

describe('when generation fails for want of a key', () => {
  it('leaves an unrelated failure on the summary, where the retry is', async () => {
    // Only a missing key is answered by the key step. Redirecting on every
    // error would send someone whose model timed out to a page asking for a
    // credential they already have.
    mockGenerateTrainingPlan.mockRejectedValue(new Error('The model timed out.'))
    setupStore({ stravaConnection: null })
    render(<OnboardingPage />)
    const user = userEvent.setup()
    await screen.findByText('Step 1 of 5')
    await walkTheQuestions(user)

    await user.click(screen.getByRole('button', { name: /Generate My 14-Day Training Plan/i }))

    expect(await screen.findByText('The model timed out.')).toBeInTheDocument()
    expect(screen.getByText('Ready to Go!')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /add your gemini key/i })).not.toBeInTheDocument()
  })
})
