import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SettingsPage from './SettingsPage'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const {
  mockUpdateCurrentUser,
  mockDeleteCurrentUser,
  mockEstimateFTP,
  mockUpdateMetrics,
  mockRecalculateAll,
  mockImportProgress,
} = vi.hoisted(() => ({
  mockUpdateCurrentUser: vi.fn(),
  mockDeleteCurrentUser: vi.fn(),
  mockEstimateFTP: vi.fn(),
  mockUpdateMetrics: vi.fn(),
  mockRecalculateAll: vi.fn(),
  mockImportProgress: vi.fn(),
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  deleteCurrentUser: mockDeleteCurrentUser,
  estimateFTP: mockEstimateFTP,
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

// StravaConnect uses strava services; stub the component
vi.mock('../components/StravaConnect', () => ({
  default: () => <div data-testid="strava-connect-stub" />,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
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
    aiProvider: 'openai',
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
    setup()
    expect(screen.getByRole('heading', { name: /AI Provider/i })).toBeInTheDocument()
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
    expect(screen.getByText('Google Gemini')).toBeInTheDocument()
  })

  it('displays the logged-in user email', () => {
    setup()
    expect(screen.getByText(/alice@example\.com/)).toBeInTheDocument()
  })

  it('saves the selected AI provider when Save is clicked', async () => {
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
    setup()
    const aiSection = screen.getByRole('heading', { name: /AI Provider/i }).closest('div')!
    await userEvent.click(within(aiSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(screen.getByText('AI settings saved!')).toBeInTheDocument()
    })
  })

  it('calls resetAll and deleteCurrentUser when the reset is confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    await waitFor(() => {
      expect(mockDeleteCurrentUser).toHaveBeenCalledWith('tok-123')
      expect(useAppStore.getState().authToken).toBeNull()
    })
  })

  it('does not call deleteCurrentUser when the reset dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    expect(mockDeleteCurrentUser).not.toHaveBeenCalled()
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

    const ftpSection = screen.getByRole('heading', { name: /FTP Management/i }).closest('div')!
    expect(within(ftpSection).getByDisplayValue('285')).toBeInTheDocument()
    expect(within(ftpSection).getByRole('button', { name: /Save FTP/i })).toBeDisabled()

    const hrSection = screen.getByRole('heading', { name: /Heart Rate Settings/i }).closest('div')!
    expect(within(hrSection).getByDisplayValue('188')).toBeInTheDocument()
    expect(within(hrSection).getByDisplayValue('52')).toBeInTheDocument()
    expect(within(hrSection).getByText(/188 bpm/i)).toBeInTheDocument()
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

  it('saves the updated name when Save is clicked in the Account section', async () => {
    mockUpdateCurrentUser.mockResolvedValue({ profile: { ...baseProfile, name: 'Alice Updated' } })
    setup()

    const nameInput = screen.getByPlaceholderText('Your name')
    await userEvent.clear(nameInput)
    await userEvent.type(nameInput, 'Alice Updated')

    const accountSection = screen.getByRole('heading', { name: /Account/i }).closest('div')!
    await userEvent.click(within(accountSection).getByRole('button', { name: /save/i }))

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

    const accountSection = screen.getByRole('heading', { name: /Account/i }).closest('div')!
    await userEvent.click(within(accountSection).getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(screen.getByText('Name saved!')).toBeInTheDocument()
    })
  })

  it('renders the Heart Rate Settings section', () => {
    setup()
    expect(screen.getByRole('heading', { name: /Heart Rate Settings/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save & Estimate FTP/i })).toBeInTheDocument()
  })

  it('Save & Estimate FTP is disabled when no HR inputs are filled', () => {
    setup()
    const btn = screen.getByRole('button', { name: /Save & Estimate FTP/i })
    expect(btn).toBeDisabled()
  })

  it('Save & Estimate FTP becomes enabled when Max HR is entered', async () => {
    setup()
    const input = screen.getByPlaceholderText(/185/)
    await userEvent.type(input, '188')
    const btn = screen.getByRole('button', { name: /Save & Estimate FTP/i })
    expect(btn).not.toBeDisabled()
  })

  it('calls estimateFTP with entered HR values and shows estimate panel when FTP is found', async () => {
    mockEstimateFTP.mockResolvedValue({ estimatedFTP: 248, source: 'ftp_estimation' })
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

    expect(screen.getByText('Imported')).toBeInTheDocument()
    expect(screen.getByText('Skipped')).toBeInTheDocument()
    expect(screen.getByText(/Broken ride/)).toBeInTheDocument()
    expect(screen.getByText(/Stream download failed/)).toBeInTheDocument()
  })
})
