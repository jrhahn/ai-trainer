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

const mockApiFetch = vi.fn()
vi.mock('../services/api', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
  API_BASE: 'http://localhost:8000/api/v1',
}))

const mockLoadUserData = vi.fn()

function setup(search = '') {
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
  mockApiFetch.mockResolvedValue({ processed: 42, skipped: 0 })
  mockNavigate.mockReset()
})

describe('StravaCallbackPage', () => {
  it('shows an error when no success or error param is present', () => {
    setup('')
    expect(screen.getByText('Connection Failed')).toBeInTheDocument()
    expect(screen.getByText(/No success confirmation received/)).toBeInTheDocument()
  })

  it('shows an error when the error query param is present', () => {
    setup('?error=access_denied')
    expect(screen.getByText('Connection Failed')).toBeInTheDocument()
    expect(screen.getByText('access_denied')).toBeInTheDocument()
  })

  it('shows the importing step while the API call is in flight', async () => {
    // Never resolves — keeps the component in the importing state
    mockApiFetch.mockReturnValue(new Promise(() => {}))
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/Importing ride history/i)).toBeInTheDocument()
    })
  })

  it('shows done with ride count after successful import', async () => {
    setup('?success=1')

    await waitFor(() => {
      expect(screen.getByText(/All set/i)).toBeInTheDocument()
      expect(screen.getByText(/42 rides imported/i)).toBeInTheDocument()
    })

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'), { timeout: 3000 })
  })

  it('still reaches done and redirects even when import fails', async () => {
    mockApiFetch.mockRejectedValue(new Error('network error'))
    setup('?success=1')

    await waitFor(() => expect(screen.getByText(/All set/i)).toBeInTheDocument())
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'), { timeout: 3000 })
  })

  it('navigates to /settings when the Go to Settings button is clicked on the error screen', async () => {
    setup('?error=access_denied')

    await userEvent.click(screen.getByRole('button', { name: /go to settings/i }))

    expect(mockNavigate).toHaveBeenCalledWith('/settings')
  })
})
