import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntervalsConnect from './IntervalsConnect'
import { IMPORT_POLL_MS } from '../hooks/useIntervalsImport'
import { useAppStore } from '../store/useAppStore'

const {
  mockDisconnectIntervals,
  mockCheckIntervals,
  mockSaveIntervalsConnection,
  mockStartImport,
  mockImportProgress,
} = vi.hoisted(() => ({
  mockDisconnectIntervals: vi.fn(),
  mockCheckIntervals: vi.fn(),
  mockSaveIntervalsConnection: vi.fn(),
  mockStartImport: vi.fn(),
  mockImportProgress: vi.fn(),
}))

const { mockSetPlan, mockFetchPlan, mockFetchChat, mockFetchUser } = vi.hoisted(() => ({
  mockSetPlan: vi.fn(),
  mockFetchPlan: vi.fn(),
  mockFetchChat: vi.fn(),
  mockFetchUser: vi.fn(),
}))

vi.mock('../queries/plan', () => ({ setPlan: mockSetPlan }))
vi.mock('../services/user', () => ({
  fetchRideMetricsHistory: vi.fn().mockResolvedValue([]),
  fetchMetricsHistory: vi.fn().mockResolvedValue([]),
  fetchTrainingPlan: mockFetchPlan,
  fetchChatHistory: mockFetchChat,
  fetchCurrentUser: mockFetchUser,
}))

vi.mock('../services/intervals', () => ({
  disconnectIntervals: mockDisconnectIntervals,
  checkIntervals: mockCheckIntervals,
  saveIntervalsConnection: mockSaveIntervalsConnection,
  startIntervalsConnectImport: mockStartImport,
  getIntervalsImportProgress: mockImportProgress,
}))

const IMPORT = { status: 'running', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' }

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({
    authToken: 'tok-123',
    intervalsConnection: null,
    intervalsAnalysisComplete: true,
  })
  vi.clearAllMocks()
  mockSaveIntervalsConnection.mockResolvedValue({
    athleteId: 'i123',
    athleteName: 'Alex Interval',
  })
  mockDisconnectIntervals.mockResolvedValue(undefined)
  mockCheckIntervals.mockResolvedValue({ usable: 0, fromStrava: 0 })
  mockStartImport.mockResolvedValue({ status: 'started' })
  mockImportProgress.mockResolvedValue({ ...IMPORT, status: 'idle' })
})

async function connectWithKey() {
  render(<IntervalsConnect />)
  await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
  await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))
}

describe('IntervalsConnect', () => {
  it('names no account for a key saved without an ID, which is "0"', () => {
    useAppStore.setState({ intervalsConnection: { athleteId: '0', athleteName: '' } })
    render(<IntervalsConnect />)

    expect(screen.getByText('Connected to Intervals.icu')).toBeInTheDocument()
    expect(screen.queryByText(/Athlete 0/)).not.toBeInTheDocument()
  })

  it('names the account by its name and never by its ID', () => {
    useAppStore.setState({ intervalsConnection: { athleteId: 'i600858', athleteName: null } })
    const { unmount } = render(<IntervalsConnect />)
    expect(screen.queryByText(/i600858/)).toBeNull()
    unmount()

    useAppStore.setState({ intervalsConnection: { athleteId: 'i600858', athleteName: 'Alice' } })
    render(<IntervalsConnect />)
    expect(screen.getByText('Alice')).toBeInTheDocument()
  })

  it('saves Intervals.icu credentials and updates the app store', async () => {
    render(<IntervalsConnect />)

    await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
    await userEvent.clear(screen.getByLabelText(/Athlete ID/i))
    await userEvent.type(screen.getByLabelText(/Athlete ID/i), 'i123')
    await userEvent.type(screen.getByLabelText(/Athlete Name/i), 'Alex Interval')
    await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))

    await waitFor(() => {
      expect(mockSaveIntervalsConnection).toHaveBeenCalledWith('tok-123', {
        apiKey: 'secret-key',
        athleteId: 'i123',
        athleteName: 'Alex Interval',
      })
    })
    expect(useAppStore.getState().intervalsConnection).toEqual({
      athleteId: 'i123',
      athleteName: 'Alex Interval',
    })
    expect(useAppStore.getState().intervalsAnalysisComplete).toBe(false)
  })

  it('keeps the general message for a save that fails for another reason', async () => {
    mockSaveIntervalsConnection.mockRejectedValueOnce(Object.assign(new Error('Server error'), { status: 500 }))
    render(<IntervalsConnect />)

    await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
    await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))

    expect(
      await screen.findByText('Could not save the Intervals.icu connection. Please check the credentials and try again.')
    ).toBeInTheDocument()
  })

  it('keeps a key intervals.icu refused out, with the form and the key still there (ai-trainer-ops#212)', async () => {
    mockSaveIntervalsConnection.mockRejectedValueOnce(
      Object.assign(new Error('Intervals.icu rejected the API key'), { status: 403 })
    )
    const onChange = vi.fn()
    render(<IntervalsConnect onChange={onChange} />)

    await userEvent.type(screen.getByLabelText(/API Key/i), 'typed-wrong')
    await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))

    expect(
      await screen.findByText('intervals.icu did not accept this key. Copy the whole API key again and paste it here.')
    ).toBeInTheDocument()
    expect(screen.queryByText('Connected to Intervals.icu')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/API Key/i)).toHaveValue('typed-wrong')
    expect(useAppStore.getState().intervalsConnection).toBeNull()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('tells the page after connecting and after disconnecting', async () => {
    const onChange = vi.fn()
    render(<IntervalsConnect onChange={onChange} />)

    await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
    await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))

    await userEvent.click(await screen.findByRole('button', { name: /Disconnect/i }))
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2))
  })

  it('still sends 0 when the athlete leaves the ID blank', async () => {
    // The field used to be prefilled with `0` — the backend's word for "the
    // athlete this key belongs to", and a number no athlete can know
    // (ai-trainer-ops#50). It starts blank now, and `0` is still what goes to
    // the server, so this is a change to what the form *claims* and not to what
    // it does. Pinned, because a refactor that quietly started sending an empty
    // athlete id would connect the account to nothing.
    render(<IntervalsConnect />)

    await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
    expect(screen.getByLabelText(/Athlete ID/i)).toHaveValue('')
    await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))

    await waitFor(() => {
      expect(mockSaveIntervalsConnection).toHaveBeenCalledWith('tok-123', {
        apiKey: 'secret-key',
        athleteId: '0',
        athleteName: '',
      })
    })
  })

  it('says step by step where the key is', () => {
    render(<IntervalsConnect />)

    expect(screen.getByRole('link', { name: /your intervals\.icu settings/i })).toHaveAttribute(
      'href',
      'https://intervals.icu/settings',
    )
    expect(screen.getByText('Developer Settings')).toBeInTheDocument()
    // The ID is for a key that reaches more than one athlete; most never need it.
    expect(screen.getByText(/Athlete ID and name \(optional\)/)).toBeInTheDocument()
  })

  it('asks first for the device that records the rides, because Strava alone is not enough', () => {
    render(<IntervalsConnect />)

    const steps = screen.getAllByRole('listitem')
    expect(steps[0]).toHaveTextContent(/connect the device or app that records your rides/)
    expect(steps[0]).toHaveTextContent(/Strava alone is not enough/)
  })

  it.each([
    [7, 'The key works: 7 activities from the last month found.'],
    // One is singular (ai-trainer-ops#121).
    [1, 'The key works: 1 activity from the last month found.'],
    // Counted in full by the server now, not capped at a sample of ten.
    [23, 'The key works: 23 activities from the last month found.'],
    [0, 'The key works, but there are no activities from the last month yet.'],
  ])('proves the key with a first read: %i activities', async (usable, text) => {
    mockCheckIntervals.mockResolvedValue({ usable, fromStrava: 0 })

    await connectWithKey()

    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(mockCheckIntervals).toHaveBeenCalledWith('tok-123')
    expect(screen.queryByText(/came to intervals\.icu from Strava/)).not.toBeInTheDocument()
  })

  it('says when the rides came from Strava and cannot reach the coach, and what to do', async () => {
    mockCheckIntervals.mockResolvedValue({ usable: 0, fromStrava: 5 })

    await connectWithKey()

    expect(
      await screen.findByText('The key works, but none of your rides from the last month can reach the coach yet.'),
    ).toBeInTheDocument()
    const note = screen.getByRole('status')
    expect(note).toHaveTextContent('5 rides from the last month came to intervals.icu from Strava.')
    expect(note).toHaveTextContent(/connect the device or app that records them/)
    expect(note).toHaveTextContent(/request your archive from Strava/)
  })

  it('names one withheld ride in the singular, next to the rides that do arrive', async () => {
    mockCheckIntervals.mockResolvedValue({ usable: 4, fromStrava: 1 })

    await connectWithKey()

    expect(await screen.findByText('The key works: 4 activities from the last month found.')).toBeInTheDocument()
    const withheld = screen.getByText(/1 ride from the last month came to intervals\.icu from Strava\./)
    expect(withheld.closest('[role="status"]')).not.toBeNull()
  })

  it('says so when intervals.icu refuses the key it just saved', async () => {
    mockCheckIntervals.mockRejectedValue(Object.assign(new Error('rejected'), { status: 403 }))

    await connectWithKey()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'intervals.icu did not accept this key',
    )
  })

  it.each([502, 500, undefined])(
    'does not blame the key for an outage (status %s)',
    async (status) => {
      mockCheckIntervals.mockRejectedValue(Object.assign(new Error('down'), { status }))

      await connectWithKey()

      expect(await screen.findByRole('status')).toHaveTextContent(
        'intervals.icu did not answer just now',
      )
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    },
  )

  it('forgets the check when the athlete disconnects', async () => {
    mockCheckIntervals.mockResolvedValue({ usable: 1, fromStrava: 0 })
    await connectWithKey()
    expect(await screen.findByText(/The key works/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Disconnect/i }))

    await waitFor(() => expect(screen.queryByText(/The key works/)).not.toBeInTheDocument())
  })

  it('shows the key as dots, and its hint while the eye is held (ai-trainer-ops#307)', () => {
    useAppStore.setState({ intervalsConnection: { athleteId: '0', keyHint: '7q2w…a7sd' } })
    render(<IntervalsConnect />)
    const key = screen.getByTestId('intervals-key')
    const eye = screen.getByRole('button', { name: 'Hold to show key' })

    expect(key).toHaveTextContent('••••••••••••••••')
    fireEvent.pointerDown(eye)
    expect(key).toHaveTextContent('7q2w…a7sd')
    fireEvent.pointerUp(eye)
    expect(key).toHaveTextContent('••••••••••••••••')
  })

  it('offers no eye for a key without a hint', () => {
    useAppStore.setState({ intervalsConnection: { athleteId: '0', keyHint: null } })
    render(<IntervalsConnect />)

    expect(screen.getByTestId('intervals-key')).toHaveTextContent('••••••••••••••••')
    expect(screen.queryByRole('button', { name: 'Hold to show key' })).not.toBeInTheDocument()
  })

  it('shows connected state and disconnects Intervals.icu', async () => {
    useAppStore.setState({
      intervalsConnection: {
        athleteId: 'i123',
        athleteName: 'Alex Interval',
      },
      intervalsAnalysisComplete: true,
    })

    render(<IntervalsConnect />)

    expect(screen.getByText('Connected to Intervals.icu')).toBeInTheDocument()
    expect(screen.getByText('Alex Interval')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Disconnect/i }))

    await waitFor(() => {
      expect(mockDisconnectIntervals).toHaveBeenCalledWith('tok-123')
    })
    expect(useAppStore.getState().intervalsConnection).toBeNull()
    expect(useAppStore.getState().intervalsAnalysisComplete).toBe(false)
  })
})

describe('connecting takes the last six weeks in (ai-trainer-ops#306)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  async function poll() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS)
    })
  }

  const importStatus = () => screen.getAllByRole('status').map((el) => el.textContent).join(' | ')

  it('imports at once when the key finds rides, and shows the coach looking and what came of it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockCheckIntervals.mockResolvedValue({ usable: 5, fromStrava: 0 })
    mockImportProgress
      .mockResolvedValueOnce({ ...IMPORT, status: 'idle' })
      .mockResolvedValueOnce({ ...IMPORT, total: 5, processed: 2 })
      .mockResolvedValueOnce({ ...IMPORT, status: 'analysing', total: 5, processed: 5, imported: 5 })
      .mockResolvedValue({ ...IMPORT, status: 'done', total: 5, processed: 5, imported: 5, analysed: true, planChanges: 1 })
    const plan = [{ date: '2026-11-02', workoutType: 'recovery', title: 'Easy spin' }]
    const chat = [{ role: 'assistant', content: 'I eased Thursday.', timestamp: '2026-10-26T20:00:00Z' }]
    const assessment = { riderType: 'allrounder', notes: 'Steady base.' }
    mockFetchPlan.mockResolvedValue(plan)
    mockFetchChat.mockResolvedValue(chat)
    mockFetchUser.mockResolvedValue({ riderAssessment: assessment, lastIntervalsActivityId: 77 })

    await connectWithKey()

    await waitFor(() => expect(mockStartImport).toHaveBeenCalledWith('tok-123'))
    expect(importStatus()).toContain('Importing…')
    await poll()
    expect(importStatus()).toContain('Importing… 2 of 5 activities')
    await poll()
    expect(importStatus()).toContain('5 imported. Your coach is looking at them…')
    await poll()
    await waitFor(() =>
      expect(importStatus()).toContain('5 imported · Your coach looked at them and adjusted your plan. The chat says why.'),
    )
    // What the coach changed, said and wrote is the server's now.
    await waitFor(() => expect(mockSetPlan).toHaveBeenCalledWith(plan))
    const state = useAppStore.getState()
    expect(state.intervalsAnalysisComplete).toBe(true)
    expect(state.chatHistory).toEqual(chat)
    expect(state.riderAssessment).toEqual(assessment)
    expect(state.lastIntervalsActivityId).toBe(77)
  })

  it('says so when the coach looked and the plan stays', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockCheckIntervals.mockResolvedValue({ usable: 1, fromStrava: 0 })
    mockImportProgress
      .mockResolvedValueOnce({ ...IMPORT, status: 'idle' })
      .mockResolvedValue({ ...IMPORT, status: 'done', total: 1, processed: 1, imported: 1, analysed: true, planChanges: 0 })
    mockFetchPlan.mockResolvedValue([])
    mockFetchChat.mockResolvedValue([])
    mockFetchUser.mockResolvedValue({ riderAssessment: null, lastIntervalsActivityId: null })

    await connectWithKey()
    await waitFor(() => expect(mockStartImport).toHaveBeenCalled())
    await poll()

    await waitFor(() => expect(importStatus()).toContain('1 imported · Your coach looked at them. Your plan stays as it is.'))
  })

  it('imports nothing for a key that finds no rides', async () => {
    mockCheckIntervals.mockResolvedValue({ usable: 0, fromStrava: 3 })

    await connectWithKey()

    expect(await screen.findByText(/none of your rides from the last month can reach the coach yet/)).toBeInTheDocument()
    expect(mockStartImport).not.toHaveBeenCalled()
  })

  it('shows an import still running when the card opens', async () => {
    useAppStore.setState({ intervalsConnection: { athleteId: '0', keyHint: null } })
    mockImportProgress.mockResolvedValue({ ...IMPORT, total: 10, processed: 3 })

    render(<IntervalsConnect />)

    expect(await screen.findByText('Importing… 3 of 10 activities')).toBeInTheDocument()
  })

  it('says why when the import cannot start', async () => {
    mockCheckIntervals.mockResolvedValue({ usable: 2, fromStrava: 0 })
    mockStartImport.mockRejectedValue(new Error('Intervals.icu not connected'))

    await connectWithKey()

    expect(await screen.findByText('Intervals.icu not connected')).toBeInTheDocument()
  })
})
