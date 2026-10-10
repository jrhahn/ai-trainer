import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SettingsPage from './SettingsPage'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const {
  mockUpdateCurrentUser,
  mockDeleteCurrentUser,
  mockExportAccountData,
  mockEstimateFTP,
  mockUpdateMetrics,
  mockRecalculateAll,
  mockImportProgress,
} = vi.hoisted(() => ({
  mockUpdateCurrentUser: vi.fn(),
  mockDeleteCurrentUser: vi.fn(),
  mockExportAccountData: vi.fn(),
  mockEstimateFTP: vi.fn(),
  mockUpdateMetrics: vi.fn(),
  mockRecalculateAll: vi.fn(),
  mockImportProgress: vi.fn(),
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  deleteCurrentUser: mockDeleteCurrentUser,
  exportAccountData: mockExportAccountData,
  estimateFTP: mockEstimateFTP,
  fetchAIKeyStatus: vi.fn().mockResolvedValue({ provider: 'openai', hasOpenaiKey: false, hasGeminiKey: false }),
  saveAIKey: vi.fn(),
  deleteAIKey: vi.fn(),
  testAIKey: vi.fn(),
}))

vi.mock('../hooks/useMetricsPipeline', () => ({
  useMetricsPipeline: () => ({
    updateMetrics: mockUpdateMetrics,
    recalculateAll: mockRecalculateAll,
    isPending: false,
    error: null,
  }),
}))

vi.mock('../hooks/useImportProgress', () => ({
  useImportProgress: mockImportProgress,
}))

vi.mock('../components/AIKeySettings', () => ({
  default: () => <div data-testid="ai-key-settings-stub" />,
}))

// StravaConnect uses strava services; stub the component
vi.mock('../components/StravaConnect', () => ({
  default: () => <div data-testid="strava-connect-stub" />,
}))

vi.mock('../components/IntervalsConnect', () => ({
  default: () => <div data-testid="intervals-connect-stub" />,
}))

vi.mock('../components/FitFileUpload', () => ({
  default: () => <div data-testid="fit-file-upload-stub">Choose .fit files</div>,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'general_fitness',
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useAppStore.setState({
    authToken: 'tok-123',
    userProfile: baseProfile,
    isExpertMode: false,
    stravaAutoSyncEnabled: true,
    intervalsAutoSyncEnabled: true,
    aiProvider: 'openai',
    ftpPlausibilityWarning: null,
  })
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue({})
  mockDeleteCurrentUser.mockResolvedValue(undefined)
  mockEstimateFTP.mockResolvedValue({ estimatedFTP: null, source: 'none' })
  mockUpdateMetrics.mockResolvedValue({ updated: 10, ftpUsed: 250 })
  mockRecalculateAll.mockResolvedValue({ updated: 10, ftpUsed: 250 })
  mockImportProgress.mockReturnValue({
    status: 'idle',
    total: 0,
    processed: 0,
    imported: 0,
    skipped: 0,
    failedActivities: [],
    error: '',
  })
})

describe('SettingsPage', () => {
  it('renders the AI provider section', () => {
    useAppStore.setState({ isExpertMode: true })
    setup()
    expect(screen.getByRole('heading', { name: /AI Provider/i })).toBeInTheDocument()
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
    expect(screen.getByText('Google Gemini')).toBeInTheDocument()
  })

  it('points to the BYOK section instead of claiming keys cannot be supplied', () => {
    /*
     * The old copy said "the browser no longer stores or sends provider API
     * keys". That predated BYOK and became the opposite of true, so the page
     * told the reader there was nothing to enter while offering the field
     * hundreds of lines further down — which is how someone gave up looking
     * for it entirely.
     */
    useAppStore.setState({ isExpertMode: true })
    setup()

    expect(screen.queryByText(/no longer stores or sends/i)).not.toBeInTheDocument()
    const pointer = screen.getByRole('link', { name: /your ai provider key/i })
    expect(pointer).toHaveAttribute('href', '#your-ai-provider-key')
  })

  it('displays the logged-in user email', () => {
    setup()
    expect(screen.getByText(/alice@example\.com/)).toBeInTheDocument()
  })

  it('saves the selected AI provider when Save is clicked', async () => {
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.click(screen.getByText('Google Gemini'))
    // Click the Save button inside the AI Provider section (first Save button)
    const aiSection = screen.getByRole('heading', { name: /AI Provider/i }).closest('div')!
    await userEvent.click(within(aiSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { aiProvider: 'gemini' })
    })
    expect(useAppStore.getState().aiProvider).toBe('gemini')
  })

  it('shows a success confirmation after saving', async () => {
    useAppStore.setState({ isExpertMode: true })
    setup()
    const aiSection = screen.getByRole('heading', { name: /AI Provider/i }).closest('div')!
    await userEvent.click(within(aiSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(screen.getByText('AI settings saved!')).toBeInTheDocument()
    })
  })

  it('calls resetAll and deleteCurrentUser when the reset is confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue('Str0ng!Pass')
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    await waitFor(() => {
      expect(mockDeleteCurrentUser).toHaveBeenCalledWith('tok-123', 'Str0ng!Pass')
      expect(useAppStore.getState().authToken).toBeNull()
    })
  })

  it('downloads the account export as a JSON file (ai-trainer-ops#6)', async () => {
    mockExportAccountData.mockResolvedValue({ format: 'ai-trainer-account-export', tables: {} })
    const createObjectURL = vi.fn().mockReturnValue('blob:export')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    setup()

    await userEvent.click(screen.getByRole('button', { name: /export my data/i }))

    await waitFor(() => {
      expect(mockExportAccountData).toHaveBeenCalledWith('tok-123')
      expect(click).toHaveBeenCalled()
    })
    const anchor = click.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toMatch(/^ai-trainer-export-\d{4}-\d{2}-\d{2}\.json$/)
    expect(anchor.href).toBe('blob:export')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:export')
    expect(screen.queryByText(/export failed/i)).not.toBeInTheDocument()
    vi.unstubAllGlobals()
    click.mockRestore()
  })

  it('does not request an export without a session', async () => {
    useAppStore.setState({ authToken: null })
    setup()

    await userEvent.click(screen.getByRole('button', { name: /export my data/i }))

    expect(mockExportAccountData).not.toHaveBeenCalled()
  })

  it('says so when the account export fails', async () => {
    mockExportAccountData.mockRejectedValue(new Error('boom'))
    setup()

    await userEvent.click(screen.getByRole('button', { name: /export my data/i }))

    expect(await screen.findByText('Export failed. Please try again.')).toBeInTheDocument()
  })

  it('does not call deleteCurrentUser when the reset dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    expect(mockDeleteCurrentUser).not.toHaveBeenCalled()
  })

  it('does not delete the account when the password prompt is dismissed', async () => {
    // The confirm is the easy half. Someone who clicks through it and then
    // cancels at the password has not asked for the account to go
    // (ai-trainer-ops#35).
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue(null)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    expect(mockDeleteCurrentUser).not.toHaveBeenCalled()
    expect(useAppStore.getState().authToken).toBe('tok-123')
  })

  it('keeps the session when the deletion is refused', async () => {
    // A wrong password comes back as a 401. Clearing the session anyway would
    // log the athlete out of an account that still exists.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue('wrong')
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    mockDeleteCurrentUser.mockRejectedValueOnce(new Error('Incorrect password.'))
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalled()
    })
    expect(useAppStore.getState().authToken).toBe('tok-123')
  })

  it('reports a non-Error rejection without showing undefined', async () => {
    // The fallback half of `e instanceof Error ? e.message : ...`. A rejection
    // that is not an Error is unlikely, but the alert it produces is the one
    // the athlete reads after being refused — "undefined" would be worse than
    // no dialog at all.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue('Str0ng!Pass')
    const alerted = vi.spyOn(window, 'alert').mockImplementation(() => {})
    mockDeleteCurrentUser.mockRejectedValueOnce('not an Error object')
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    await waitFor(() => {
      expect(alerted).toHaveBeenCalledWith('The account could not be deleted.')
    })
    expect(useAppStore.getState().authToken).toBe('tok-123')
  })

  // Each of these three inputs has a visible <label> that sits *beside* it and
  // carries no htmlFor, so nothing associated the two: a screen reader
  // announced an unnamed text box, and the tests above had to reach for
  // `getByPlaceholderText` and `getByDisplayValue` within a section to find
  // them at all. That workaround is the smell. Addressing them by their
  // accessible name is both the accessible behaviour and what the browser spec
  // in `e2e/stubbed/onboarding-complete.spec.ts` uses (ai-trainer-ops#46).
  it('names the profile inputs so they can be addressed the way a screen reader does', () => {
    useAppStore.setState({
      userProfile: { ...baseProfile, currentFTP: 237, maxHeartRate: 191 },
    })

    setup()

    expect(screen.getByLabelText('Display Name')).toHaveValue('Alice')
    // Numbers, not strings: both are `type="number"`, so jsdom reports the
    // value as a number. (Playwright's `toHaveValue` is the opposite and always
    // gives a string, which is why the browser spec compares to '237'.)
    expect(screen.getByLabelText('Current FTP (watts)')).toHaveValue(237)
    expect(screen.getByLabelText('Max Heart Rate (bpm)')).toHaveValue(191)
  })

  it('says which account is signed in, which the summary step never could (#40)', () => {
    setup()

    expect(screen.getByText(`Signed in as ${baseProfile.email}.`)).toBeInTheDocument()
  })

  it('renders the Display Name input with the current user name', () => {
    setup()
    const nameInput = screen.getByPlaceholderText('Your name')
    expect(nameInput).toHaveValue('Alice')
  })

  it('fills FTP and heart-rate inputs from the current profile', () => {
    useAppStore.setState({
      userProfile: {
        ...baseProfile,
        currentFTP: 285,
        maxHeartRate: 188,
        restingHeartRate: 52,
      },
    })

    setup()

    const ftpSection = screen.getByRole('heading', { name: /^FTP$/ }).closest('div')!
    expect(within(ftpSection).getByDisplayValue('285')).toBeInTheDocument()
    expect(within(ftpSection).getByRole('button', { name: /Save FTP/i })).toBeDisabled()

    const hrSection = screen.getByRole('heading', { name: /Heart Rate Settings/i }).closest('div')!
    expect(within(hrSection).getByDisplayValue('188')).toBeInTheDocument()
    expect(within(hrSection).getByDisplayValue('52')).toBeInTheDocument()
    expect(within(hrSection).getByText(/188 bpm/i)).toBeInTheDocument()
  })

  it('shows a stored threshold pace as minutes and seconds, never as a decimal', () => {
    useAppStore.setState({
      userProfile: { ...baseProfile, thresholdPaceSecondsPerKm: 250 },
    })

    setup()

    const paceSection = screen
      .getByRole('heading', { name: /Running Threshold Pace/i })
      .closest('div')!
    expect(within(paceSection).getByDisplayValue('4:10')).toBeInTheDocument()
    expect(within(paceSection).queryByDisplayValue('250')).not.toBeInTheDocument()
    expect(within(paceSection).getByRole('button', { name: /Save pace/i })).toBeDisabled()
  })

  it('saves a threshold pace as a number of seconds', async () => {
    useAppStore.setState({ userProfile: { ...baseProfile } })
    mockUpdateCurrentUser.mockResolvedValue({
      profile: { ...baseProfile, thresholdPaceSecondsPerKm: 245 },
      ftpPlausibilityWarning: null,
    })

    setup()

    const input = screen.getByLabelText(/Running threshold pace/i)
    await userEvent.type(input, '4:05')
    await userEvent.click(screen.getByRole('button', { name: /Save pace/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith(expect.anything(), {
        thresholdPaceSecondsPerKm: 245,
      })
    })
  })

  it('reports a failed pace save rather than leaving the button spinning', async () => {
    useAppStore.setState({ userProfile: { ...baseProfile } })
    mockUpdateCurrentUser.mockRejectedValue(new Error('network'))

    setup()

    const input = screen.getByLabelText(/Running threshold pace/i)
    await userEvent.type(input, '4:05')
    await userEvent.click(screen.getByRole('button', { name: /Save pace/i }))

    expect(await screen.findByText(/Failed to save threshold pace/i)).toBeInTheDocument()
    // The spinner has to stop, or the athlete cannot retry.
    expect(screen.getByRole('button', { name: /Save pace/i })).toBeEnabled()
  })

  it('does not attempt a pace save before the profile has loaded', async () => {
    useAppStore.setState({ userProfile: null })

    setup()

    const input = screen.getByLabelText(/Running threshold pace/i)
    await userEvent.type(input, '4:05')
    await userEvent.click(screen.getByRole('button', { name: /Save pace/i }))

    expect(mockUpdateCurrentUser).not.toHaveBeenCalled()
  })

  it('refuses a pace that is not minutes and seconds instead of sending a guess', async () => {
    useAppStore.setState({ userProfile: { ...baseProfile } })

    setup()

    const input = screen.getByLabelText(/Running threshold pace/i)
    await userEvent.type(input, '4.17')
    await userEvent.click(screen.getByRole('button', { name: /Save pace/i }))

    expect(await screen.findByText(/minutes:seconds per km/i)).toBeInTheDocument()
    expect(mockUpdateCurrentUser).not.toHaveBeenCalled()
  })

  it('shows the FTP plausibility warning when one is present', () => {
    useAppStore.setState({
      userProfile: { ...baseProfile, currentFTP: 300 },
      ftpPlausibilityWarning:
        'FTP of 300 W is 97% of your best 5-minute power (310 W). FTP is a ' +
        'sustainable effort below maximal aerobic power — normally 72-85 % of ' +
        'it — so this FTP looks too high.',
    })

    setup()

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/97% of your best 5-minute power/)
    expect(alert).toHaveTextContent(/looks too high/)
    // Advisory only — the entered FTP is left untouched.
    const ftpSection = screen.getByRole('heading', { name: /^FTP$/ }).closest('div')!
    expect(within(ftpSection).getByDisplayValue('300')).toBeInTheDocument()
  })

  it('shows no plausibility warning when FTP and MAP are consistent', () => {
    useAppStore.setState({
      userProfile: { ...baseProfile, currentFTP: 280 },
      ftpPlausibilityWarning: null,
    })

    setup()

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('refreshes the plausibility warning from the save response', async () => {
    useAppStore.setState({ userProfile: { ...baseProfile, currentFTP: 280 } })
    mockUpdateCurrentUser.mockResolvedValue({
      profile: { ...baseProfile, currentFTP: 500 },
      ftpPlausibilityWarning: 'FTP of 500 W is 98% of your best 5-minute power (510 W).',
    })

    setup()

    const ftpSection = screen.getByRole('heading', { name: /^FTP$/ }).closest('div')!
    const input = within(ftpSection).getByDisplayValue('280')
    await userEvent.clear(input)
    await userEvent.type(input, '500')
    await userEvent.click(within(ftpSection).getByRole('button', { name: /Save FTP/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/98% of your best 5-minute power/)
    })
    expect(useAppStore.getState().ftpPlausibilityWarning).toBe(
      'FTP of 500 W is 98% of your best 5-minute power (510 W).'
    )
  })

  it('hydrates FTP and heart-rate inputs when profile data arrives after render', async () => {
    useAppStore.setState({ userProfile: null })

    setup()

    useAppStore.setState({
      userProfile: {
        ...baseProfile,
        currentFTP: 275,
        maxHeartRate: 191,
      },
    })

    await waitFor(() => {
      expect(screen.getByDisplayValue('275')).toBeInTheDocument()
      expect(screen.getByDisplayValue('191')).toBeInTheDocument()
    })
  })

  it('saves the updated name when Save is clicked in the Name card', async () => {
    mockUpdateCurrentUser.mockResolvedValue({ profile: { ...baseProfile, name: 'Alice Updated' } })
    setup()

    const nameInput = screen.getByPlaceholderText('Your name')
    await userEvent.clear(nameInput)
    await userEvent.type(nameInput, 'Alice Updated')

    const nameSection = screen.getByRole('heading', { name: /^Name$/ }).closest('div')!
    await userEvent.click(within(nameSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { name: 'Alice Updated' })
    })
    expect(useAppStore.getState().userProfile?.name).toBe('Alice Updated')
  })

  it('shows Name saved! confirmation after saving name', async () => {
    mockUpdateCurrentUser.mockResolvedValue({ profile: { ...baseProfile, name: 'Bob' } })
    setup()

    const nameInput = screen.getByPlaceholderText('Your name')
    await userEvent.clear(nameInput)
    await userEvent.type(nameInput, 'Bob')

    const nameSection = screen.getByRole('heading', { name: /^Name$/ }).closest('div')!
    await userEvent.click(within(nameSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(screen.getByText('Name saved!')).toBeInTheDocument()
    })
  })

  it('renders the Heart Rate Settings section', () => {
    useAppStore.setState({ isExpertMode: true })
    setup()
    expect(screen.getByRole('heading', { name: /Heart Rate Settings/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save & Estimate FTP/i })).toBeInTheDocument()
  })

  it('Save & Estimate FTP is disabled when no HR inputs are filled', () => {
    useAppStore.setState({ isExpertMode: true })
    setup()
    const btn = screen.getByRole('button', { name: /Save & Estimate FTP/i })
    expect(btn).toBeDisabled()
  })

  it('Save & Estimate FTP becomes enabled when Max HR is entered', async () => {
    useAppStore.setState({ isExpertMode: true })
    setup()
    const input = screen.getByPlaceholderText(/185/)
    await userEvent.type(input, '188')
    const btn = screen.getByRole('button', { name: /Save & Estimate FTP/i })
    expect(btn).not.toBeDisabled()
  })

  it('calls estimateFTP with entered HR values and shows estimate panel when FTP is found', async () => {
    mockEstimateFTP.mockResolvedValue({ estimatedFTP: 248, source: 'ftp_estimation' })
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.type(screen.getByPlaceholderText(/185/), '185')

    await userEvent.click(screen.getByRole('button', { name: /Save & Estimate FTP/i }))

    await waitFor(() => {
      expect(mockEstimateFTP).toHaveBeenCalledWith('tok-123', {
        maxHeartRate: 185,
        restingHeartRate: undefined,
      })
      // FTP confirmation panel should appear
      expect(screen.getByText(/Step 2/i)).toBeInTheDocument()
      expect(screen.getByText(/248 W/)).toBeInTheDocument()
    })
  })

  it('shows success message when no FTP estimate is available yet', async () => {
    mockEstimateFTP.mockResolvedValue({ estimatedFTP: null, source: 'none' })
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.type(screen.getByPlaceholderText(/185/), '185')
    await userEvent.click(screen.getByRole('button', { name: /Save & Estimate FTP/i }))

    await waitFor(() => {
      expect(screen.getByText(/Heart rate values saved/i)).toBeInTheDocument()
      expect(screen.queryByText(/Step 2/i)).not.toBeInTheDocument()
    })
  })

  it('calls updateMetrics with the confirmed FTP and clears the panel', async () => {
    mockEstimateFTP.mockResolvedValue({ estimatedFTP: 260, source: 'ftp_estimation' })
    mockUpdateMetrics.mockResolvedValue({ updated: 8, ftpUsed: 260 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    useAppStore.setState({ isExpertMode: true })
    setup()

    // Step 1 – trigger estimate
    await userEvent.type(screen.getByPlaceholderText(/185/), '185')
    await userEvent.click(screen.getByRole('button', { name: /Save & Estimate FTP/i }))

    // Wait for confirmation panel
    await waitFor(() => expect(screen.getByText(/Step 2/i)).toBeInTheDocument())

    // Step 2 – confirm FTP
    await userEvent.click(screen.getByRole('button', { name: /Confirm & Recalculate/i }))

    await waitFor(() => {
      expect(mockUpdateMetrics).toHaveBeenCalledWith({ currentFTP: 260 })
      // Panel should be gone after recalculation
      expect(screen.queryByText(/Step 2/i)).not.toBeInTheDocument()
    })
  })

  it('shows Strava activity analysis progress inside the Strava settings card while running', () => {
    mockImportProgress.mockReturnValue({
      jobId: 'job-1',
      status: 'running',
      total: 10,
      processed: 4,
      imported: 2,
      skipped: 0,
      failedActivities: [],
      error: '',
    })

    setup()

    expect(screen.getByText('Strava Activity Analysis')).toBeInTheDocument()
    expect(screen.getByText('Processed activities: 4 / 10 (40%) · 2 imported')).toBeInTheDocument()
  })

  it('lists intervals.icu before Strava among the data sources (ai-trainer-ops#20)', () => {
    setup()

    const intervals = screen.getByText('Intervals.icu')
    const strava = screen.getByText('Strava')
    expect(
      intervals.compareDocumentPosition(strava) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('renders source-agnostic data source settings', () => {
    setup()

    expect(screen.getByRole('heading', { name: /Data Sources/i })).toBeInTheDocument()
    expect(screen.getByText(/Automatic sources are convenient/i)).toBeInTheDocument()
    expect(screen.getByText(/paid Strava subscription/i)).toBeInTheDocument()
    expect(screen.getByText('Strava')).toBeInTheDocument()
    expect(screen.getByText('Intervals.icu')).toBeInTheDocument()
    expect(screen.getByText('FIT files')).toBeInTheDocument()
    expect(screen.getByTestId('strava-connect-stub')).toBeInTheDocument()
    expect(screen.getByTestId('intervals-connect-stub')).toBeInTheDocument()
    expect(screen.getByTestId('fit-file-upload-stub')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /turn on automatic sync with strava/i })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /turn on automatic sync with intervals\.icu/i })).toBeInTheDocument()
  })

  it('shows connection and last-sync state for automatic sources', () => {
    useAppStore.setState({
      stravaConnection: { athleteId: 42, athleteName: 'Alice Strava' },
      intervalsConnection: { athleteId: 'i600858', athleteName: 'Alice Intervals' },
      lastStravaActivityId: 12345,
      lastIntervalsActivityId: 67890,
      stravaAutoSyncEnabled: true,
      intervalsAutoSyncEnabled: false,
    })
    useAppStore.setState({ isExpertMode: true })
    setup()

    expect(screen.getAllByText('Connected')).toHaveLength(2)
    expect(screen.getByText('Last sync cursor: 12345')).toBeInTheDocument()
    expect(screen.getByText('Last sync cursor: 67890')).toBeInTheDocument()
    expect(screen.getByText('Automatic sync: On')).toBeInTheDocument()
    expect(screen.getByText('Automatic sync: Off')).toBeInTheDocument()
  })

  it('lets users disable Strava automatic sync', async () => {
    useAppStore.setState({ stravaAutoSyncEnabled: true })
    setup()

    await userEvent.click(screen.getByRole('checkbox', { name: /turn on automatic sync with strava/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { stravaAutoSyncEnabled: false })
    })
    expect(useAppStore.getState().stravaAutoSyncEnabled).toBe(false)
  })

  it('lets users disable Intervals.icu automatic sync', async () => {
    useAppStore.setState({ intervalsAutoSyncEnabled: true })
    setup()

    await userEvent.click(screen.getByRole('checkbox', { name: /turn on automatic sync with intervals\.icu/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { intervalsAutoSyncEnabled: false })
    })
    expect(useAppStore.getState().intervalsAutoSyncEnabled).toBe(false)
  })

  it('puts the Intervals.icu toggle back and says so when saving it fails', async () => {
    useAppStore.setState({ intervalsAutoSyncEnabled: true })
    mockUpdateCurrentUser.mockRejectedValueOnce(new Error('offline'))
    setup()

    await userEvent.click(screen.getByRole('checkbox', { name: /turn on automatic sync with intervals\.icu/i }))

    expect(
      await screen.findByText('Could not save Intervals.icu sync setting. Please try again.'),
    ).toBeInTheDocument()
    expect(useAppStore.getState().intervalsAutoSyncEnabled).toBe(true)
  })

  it('shows the completed Strava import report with skipped activities', () => {
    mockImportProgress.mockReturnValue({
      jobId: 'job-1',
      status: 'done',
      total: 2,
      processed: 2,
      imported: 1,
      skipped: 1,
      failedActivities: [
        {
          activityId: 222,
          activityName: 'Broken ride',
          activityDate: '2026-04-02',
          reason: 'Stream download failed',
        },
      ],
      error: '',
    })

    setup()

    expect(screen.getByText('Strava Activity Analysis')).toBeInTheDocument()
    expect(screen.getByText('1 imported, 1 skipped')).toBeInTheDocument()
    expect(screen.getByText('Imported')).toBeInTheDocument()
    expect(screen.getByText('Skipped')).toBeInTheDocument()
    expect(screen.getByText(/Broken ride/)).toBeInTheDocument()
    expect(screen.getByText(/Stream download failed/)).toBeInTheDocument()
  })

  it('saves a new FTP value and confirms success', async () => {
    mockUpdateCurrentUser.mockResolvedValue({ profile: { ...baseProfile, currentFTP: 260 } })
    setup()

    await userEvent.type(screen.getByPlaceholderText('e.g. 250'), '260')
    await userEvent.click(screen.getByRole('button', { name: /Save FTP/ }))

    await waitFor(() =>
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { currentFTP: 260 })
    )
    expect(await screen.findByText('FTP updated to 260 W.')).toBeInTheDocument()
  })

  it('shows an error when saving FTP fails', async () => {
    mockUpdateCurrentUser.mockRejectedValue(new Error('boom'))
    setup()

    await userEvent.type(screen.getByPlaceholderText('e.g. 250'), '260')
    await userEvent.click(screen.getByRole('button', { name: /Save FTP/ }))

    expect(await screen.findByText('Failed to save FTP. Please try again.')).toBeInTheDocument()
  })

  it('recalculates metrics with the current FTP when confirmed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.click(screen.getByRole('button', { name: /Recalculate TSS \/ ATL \/ CTL/ }))

    await waitFor(() => expect(mockRecalculateAll).toHaveBeenCalledTimes(1))
    expect(
      await screen.findByText('Recalculated 10 activities using FTP 250 W.')
    ).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('does not recalculate when the confirm dialog is cancelled', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.click(screen.getByRole('button', { name: /Recalculate TSS \/ ATL \/ CTL/ }))

    expect(mockRecalculateAll).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('logs the user out when Sign Out is clicked', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: /Sign Out/ }))
    expect(useAppStore.getState().authToken).toBeNull()
  })

  it('reverts and warns when saving the Strava sync setting fails', async () => {
    mockUpdateCurrentUser.mockRejectedValue(new Error('net down'))
    setup()

    await userEvent.click(screen.getByRole('checkbox', { name: /automatic sync with strava/i }))

    expect(
      await screen.findByText('Could not save Strava sync setting. Please try again.')
    ).toBeInTheDocument()
    expect(useAppStore.getState().stravaAutoSyncEnabled).toBe(true)
  })
  it('claims only the encryption it actually has', () => {
    // A privacy sentence on a settings page is something an athlete relies on.
    // For one commit this said the keys "never leave this server", which is
    // false — intervals.icu, Strava and the AI provider each receive one. The
    // claim may only be what `EncryptedString` provides, and nothing covered
    // this text until review pointed that out.
    setup()

    expect(screen.getByText(/stored encrypted at rest/i)).toBeInTheDocument()
    expect(screen.queryByText(/never leave/i)).toBeNull()
    // And the deployment detail stays gone.
    expect(screen.queryByText(/localhost:8000/)).toBeNull()
  })

  it('marks intervals.icu as the active source when both are connected (ai-trainer-ops#49)', () => {
    // The copy has to say which source is read, or "Connected" on both while
    // only one is synced reads as a bug.
    useAppStore.setState({
      stravaConnection: { athleteId: 42, athleteName: 'Alice Strava' },
      intervalsConnection: { athleteId: 'i600858', athleteName: 'Alice Intervals' },
      stravaAutoSyncEnabled: true,
      intervalsAutoSyncEnabled: true,
    })
    setup()

    expect(screen.getAllByText('Active source')).toHaveLength(1)
    expect(
      screen.getByText(/Intervals\.icu is the primary source/)
    ).toBeInTheDocument()
  })

  it('marks Strava as the active source when intervals.icu sync is off', () => {
    useAppStore.setState({
      stravaConnection: { athleteId: 42, athleteName: 'Alice Strava' },
      intervalsConnection: { athleteId: 'i600858', athleteName: 'Alice Intervals' },
      stravaAutoSyncEnabled: true,
      intervalsAutoSyncEnabled: false,
    })
    setup()

    expect(screen.getAllByText('Active source')).toHaveLength(1)
    // The explanation belongs to the intervals-is-active case only; saying it
    // here would be false.
    expect(screen.queryByText(/Intervals\.icu is the primary source/)).toBeNull()
  })

  it('marks no source as active when neither syncs', () => {
    // Anti-vacuity for the two above: a badge rendered unconditionally would
    // satisfy both.
    useAppStore.setState({
      stravaConnection: { athleteId: 42, athleteName: 'Alice Strava' },
      intervalsConnection: { athleteId: 'i600858', athleteName: 'Alice Intervals' },
      stravaAutoSyncEnabled: false,
      intervalsAutoSyncEnabled: false,
    })
    setup()

    expect(screen.queryByText('Active source')).toBeNull()
  })
})

describe('SettingsPage sections (ai-trainer-ops#50)', () => {
  it('has five sections, each reachable from the section nav', () => {
    const { container } = setup()

    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual([
      'Profile',
      'Activities',
      'Coach & AI',
      'Account & security',
      'Your data',
    ])
    for (const link of links) {
      const target = container.querySelector(link.getAttribute('href')!)
      expect(target?.tagName).toBe('SECTION')
      expect(within(target as HTMLElement).getAllByRole('heading')[0]).toHaveTextContent(
        link.textContent!
      )
    }
  })

  it('needs no training-load vocabulary without Expert mode', () => {
    useAppStore.setState({ lastStravaActivityId: 12345, lastIntervalsActivityId: 67890 })
    const { container } = setup()

    expect(container.textContent).not.toMatch(/\b(r?TSS|ATL|CTL|TSB)\b/)
    expect(screen.queryByRole('button', { name: /Recalculate/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'AI Provider' })).toBeNull()
    expect(screen.queryByText(/Last sync cursor/)).toBeNull()
  })

  it('keeps every expert control reachable with Expert mode on', () => {
    useAppStore.setState({ isExpertMode: true, lastIntervalsActivityId: 67890 })
    setup()

    expect(screen.getByRole('button', { name: /Recalculate TSS \/ ATL \/ CTL/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'AI Provider' })).toBeInTheDocument()
    expect(screen.getByText('Last sync cursor: 67890')).toBeInTheDocument()
  })

  it('hides a pending FTP estimate when Expert mode is switched off', async () => {
    mockEstimateFTP.mockResolvedValue({ estimatedFTP: 248, source: 'ftp_estimation' })
    useAppStore.setState({ isExpertMode: true })
    setup()

    await userEvent.type(screen.getByPlaceholderText(/185/), '185')
    await userEvent.click(screen.getByRole('button', { name: /Save & Estimate FTP/i }))
    expect(await screen.findByText(/Step 2/i)).toBeInTheDocument()

    act(() => useAppStore.setState({ isExpertMode: false }))

    expect(screen.queryByText(/Step 2/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Confirm & Recalculate/ })).toBeNull()
  })

  it('saves heart rate without estimating FTP outside Expert mode', async () => {
    setup()

    await userEvent.type(screen.getByPlaceholderText(/185/), '185')
    await userEvent.click(screen.getByRole('button', { name: 'Save heart rate' }))

    expect(await screen.findByText('Heart rate values saved.')).toBeInTheDocument()
    expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { maxHeartRate: 185 })
    expect(mockEstimateFTP).not.toHaveBeenCalled()
  })
})
