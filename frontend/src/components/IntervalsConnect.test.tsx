import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntervalsConnect from './IntervalsConnect'
import { useAppStore } from '../store/useAppStore'

const {
  mockDisconnectIntervals,
  mockCheckIntervals,
  mockSaveIntervalsConnection,
} = vi.hoisted(() => ({
  mockDisconnectIntervals: vi.fn(),
  mockCheckIntervals: vi.fn(),
  mockSaveIntervalsConnection: vi.fn(),
}))

vi.mock('../services/intervals', () => ({
  disconnectIntervals: mockDisconnectIntervals,
  checkIntervals: mockCheckIntervals,
  saveIntervalsConnection: mockSaveIntervalsConnection,
}))

beforeEach(() => {
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
    expect(screen.getByRole('status')).toHaveTextContent('1 ride from the last month came to intervals.icu from Strava.')
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
