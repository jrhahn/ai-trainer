import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import StravaCallbackPage from './StravaCallbackPage'
import { useAppStore } from '../store/useAppStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const mockLoadUserData = vi.fn()

function setup(search = '') {
  // jsdom doesn't update window.location.search automatically with MemoryRouter,
  // so we manipulate the URL directly.
  Object.defineProperty(window, 'location', {
    value: { ...window.location, search },
    writable: true,
  })
  useAppStore.setState({ authToken: 'tok-123' })
  useAppStore.getState().loadUserData = mockLoadUserData
  return render(
    <MemoryRouter>
      <StravaCallbackPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockLoadUserData.mockResolvedValue(undefined)
  mockNavigate.mockReset()
})

describe('StravaCallbackPage', () => {
  it('shows the connecting loader on initial render', () => {
    Object.defineProperty(window, 'location', {
      value: { ...window.location, search: '' },
      writable: true,
    })
    // Suspend effect by using a never-resolving loadUserData
    mockLoadUserData.mockReturnValue(new Promise(() => {}))
    useAppStore.setState({ authToken: 'tok-123' })
    useAppStore.getState().loadUserData = mockLoadUserData

    render(
      <MemoryRouter>
        <StravaCallbackPage />
      </MemoryRouter>
    )

    // Without success/error params the component sets status to error
    expect(screen.getByText(/Connection Failed/i)).toBeInTheDocument()
  })

  it('shows an error when the error query param is present', () => {
    setup('?error=access_denied')
    expect(screen.getByText('Connection Failed')).toBeInTheDocument()
    expect(screen.getByText('access_denied')).toBeInTheDocument()
  })

  it('shows an error when no success or error param is present', () => {
    setup('')
    expect(screen.getByText('Connection Failed')).toBeInTheDocument()
    expect(screen.getByText(/No success confirmation received/)).toBeInTheDocument()
  })

  it('navigates to / after a successful connection', async () => {
    setup('?success=1')

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })
  })

  it('navigates to /settings when the Go to Settings button is clicked on the error screen', async () => {
    setup('?error=access_denied')

    await userEvent.click(screen.getByRole('button', { name: /go to settings/i }))

    expect(mockNavigate).toHaveBeenCalledWith('/settings')
  })

  it('logs the error to the console when the error query param is present', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    setup('?error=Token%20exchange%20failed')

    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('[StravaCallback]'),
      expect.stringContaining('Token exchange failed'),
    )
    consoleSpy.mockRestore()
  })

  it('logs a message to the console when no success or error param is present', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    setup('')

    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('[StravaCallback]'),
      expect.stringContaining('No success confirmation received'),
    )
    consoleSpy.mockRestore()
  })
})
