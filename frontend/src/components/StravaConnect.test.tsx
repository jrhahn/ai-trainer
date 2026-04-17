import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StravaConnect from './StravaConnect'
import { useAppStore } from '../store/useAppStore'

const { mockGetStravaAuthUrl, mockDisconnectStrava } = vi.hoisted(() => ({
  mockGetStravaAuthUrl: vi.fn(),
  mockDisconnectStrava: vi.fn(),
}))

vi.mock('../services/strava', () => ({
  getStravaAuthUrl: mockGetStravaAuthUrl,
  disconnectStrava: mockDisconnectStrava,
}))

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockGetStravaAuthUrl.mockResolvedValue('https://www.strava.com/oauth/authorize?client_id=123')
  mockDisconnectStrava.mockResolvedValue(undefined)
})

describe('StravaConnect', () => {
  it('shows the Connect Strava button when not connected', () => {
    useAppStore.setState({ authToken: 'tok-123', stravaConnection: null })
    render(<StravaConnect />)
    expect(screen.getByRole('button', { name: /connect strava/i })).toBeInTheDocument()
  })

  it('shows the connected athlete name when already connected', () => {
    useAppStore.setState({
      authToken: 'tok-123',
      stravaConnection: { athleteId: 7, athleteName: 'Alice Rider' },
    })
    render(<StravaConnect />)
    expect(screen.getByText('Alice Rider')).toBeInTheDocument()
    expect(screen.getByText(/Connected to Strava/i)).toBeInTheDocument()
  })

  it('shows a Disconnect button when connected', () => {
    useAppStore.setState({
      authToken: 'tok-123',
      stravaConnection: { athleteId: 7, athleteName: 'Alice Rider' },
    })
    render(<StravaConnect />)
    expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
  })

  it('clears stravaConnection after clicking Disconnect', async () => {
    useAppStore.setState({
      authToken: 'tok-123',
      stravaConnection: { athleteId: 7, athleteName: 'Alice Rider' },
    })
    render(<StravaConnect />)

    await userEvent.click(screen.getByRole('button', { name: /disconnect/i }))

    await waitFor(() => {
      expect(mockDisconnectStrava).toHaveBeenCalledWith('tok-123')
      expect(useAppStore.getState().stravaConnection).toBeNull()
    })
  })

  it('fetches the auth URL when Connect is clicked', async () => {
    useAppStore.setState({ authToken: 'tok-123', stravaConnection: null })
    render(<StravaConnect />)

    await userEvent.click(screen.getByRole('button', { name: /connect strava/i }))

    await waitFor(() => {
      expect(mockGetStravaAuthUrl).toHaveBeenCalledWith('tok-123')
    })
  })
})
