import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntervalsConnect from './IntervalsConnect'
import { useAppStore } from '../store/useAppStore'

const {
  mockDisconnectIntervals,
  mockSaveIntervalsConnection,
} = vi.hoisted(() => ({
  mockDisconnectIntervals: vi.fn(),
  mockSaveIntervalsConnection: vi.fn(),
}))

vi.mock('../services/intervals', () => ({
  disconnectIntervals: mockDisconnectIntervals,
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
})

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
