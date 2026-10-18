import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntervalsConnect from './IntervalsConnect'
import { useAppStore } from '../store/useAppStore'

const {
  mockDisconnectIntervals,
  mockGetIntervalsActivities,
  mockSaveIntervalsConnection,
} = vi.hoisted(() => ({
  mockDisconnectIntervals: vi.fn(),
  mockGetIntervalsActivities: vi.fn(),
  mockSaveIntervalsConnection: vi.fn(),
}))

vi.mock('../services/intervals', () => ({
  disconnectIntervals: mockDisconnectIntervals,
  getIntervalsActivities: mockGetIntervalsActivities,
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
  mockGetIntervalsActivities.mockResolvedValue([])
})

async function connectWithKey() {
  render(<IntervalsConnect />)
  await userEvent.type(screen.getByLabelText(/API Key/i), 'secret-key')
  await userEvent.click(screen.getByRole('button', { name: /Connect Intervals\.icu/i }))
}

describe('IntervalsConnect', () => {
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

  it.each([
    [7, 'The key works: 7 activities from the last month found.'],
    // One is singular (ai-trainer-ops#121).
    [1, 'The key works: 1 activity from the last month found.'],
    [10, 'The key works: 10 or more activities from the last month found.'],
    [0, 'The key works, but there are no activities from the last month yet.'],
  ])('proves the key with a first read: %i activities', async (count, text) => {
    mockGetIntervalsActivities.mockResolvedValue(Array.from({ length: count }, (_, id) => ({ id })))

    await connectWithKey()

    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(mockGetIntervalsActivities).toHaveBeenCalledWith('tok-123')
  })

  it('says so when intervals.icu refuses the key it just saved', async () => {
    mockGetIntervalsActivities.mockRejectedValue(Object.assign(new Error('rejected'), { status: 403 }))

    await connectWithKey()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'intervals.icu did not accept this key',
    )
  })

  it.each([502, 500, undefined])(
    'does not blame the key for an outage (status %s)',
    async (status) => {
      mockGetIntervalsActivities.mockRejectedValue(Object.assign(new Error('down'), { status }))

      await connectWithKey()

      expect(await screen.findByRole('status')).toHaveTextContent(
        'intervals.icu did not answer just now',
      )
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    },
  )

  it('forgets the check when the athlete disconnects', async () => {
    mockGetIntervalsActivities.mockResolvedValue([{ id: 1 }])
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
