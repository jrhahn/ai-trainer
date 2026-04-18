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

  it('redirects the browser to the Strava OAuth URL when Connect is clicked', async () => {
    const stravaAuthUrl = 'https://www.strava.com/oauth/authorize?client_id=123&state=abc'
    mockGetStravaAuthUrl.mockResolvedValue(stravaAuthUrl)
    useAppStore.setState({ authToken: 'tok-123', stravaConnection: null })

    // Intercept window.location.href assignments so we can assert the target URL
    // without triggering an actual navigation in jsdom.
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

    render(<StravaConnect />)
    await userEvent.click(screen.getByRole('button', { name: /connect strava/i }))

    await waitFor(() => {
      expect(capturedHref).toBe(stravaAuthUrl)
    })
  })

  it('shows an inline error banner when getting the auth URL fails', async () => {
    mockGetStravaAuthUrl.mockRejectedValue(new Error('Strava credentials are not configured on the server'))
    useAppStore.setState({ authToken: 'tok-123', stravaConnection: null })
    render(<StravaConnect />)

    await userEvent.click(screen.getByRole('button', { name: /connect strava/i }))

    await waitFor(() => {
      expect(screen.getByText(/Could not start the Strava connection/i)).toBeInTheDocument()
    })
  })

  it('logs a technical error to the console when getting the auth URL fails', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockGetStravaAuthUrl.mockRejectedValue(new Error('Strava credentials are not configured on the server'))
    useAppStore.setState({ authToken: 'tok-123', stravaConnection: null })
    render(<StravaConnect />)

    await userEvent.click(screen.getByRole('button', { name: /connect strava/i }))

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith(
        expect.stringContaining('[StravaConnect]'),
        expect.stringContaining('Strava credentials are not configured'),
        expect.any(Error),
      )
    })
    consoleSpy.mockRestore()
  })

  it('does not fetch auth URL if the user is not authenticated', async () => {
    useAppStore.setState({ authToken: null, stravaConnection: null })
    render(<StravaConnect />)

    await userEvent.click(screen.getByRole('button', { name: /connect strava/i }))

    expect(mockGetStravaAuthUrl).not.toHaveBeenCalled()
  })
})
