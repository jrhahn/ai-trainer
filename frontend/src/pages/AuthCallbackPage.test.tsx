import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import AuthCallbackPage from './AuthCallbackPage'
import { useAppStore } from '../store/useAppStore'

const { mockGetSessionToken, mockLoadUserData } = vi.hoisted(() => ({
  mockGetSessionToken: vi.fn(),
  mockLoadUserData: vi.fn(),
}))

vi.mock('../services/auth', () => ({ getSessionToken: mockGetSessionToken }))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function setup() {
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <MemoryRouter>
      <AuthCallbackPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockLoadUserData.mockResolvedValue(undefined)
})

describe('AuthCallbackPage', () => {
  it('mints an app token from the Authelia session and navigates home', async () => {
    mockGetSessionToken.mockResolvedValue('session-token')
    setup()

    expect(screen.getByText(/finishing secure sign-in/i)).toBeInTheDocument()

    await waitFor(() => {
      expect(useAppStore.getState().authToken).toBe('session-token')
      expect(mockLoadUserData).toHaveBeenCalledWith('session-token')
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
    })
  })

  it('shows a retry path when session exchange fails', async () => {
    mockGetSessionToken.mockRejectedValue(new Error('Authelia session not found'))
    setup()

    await waitFor(() => {
      expect(screen.getByText(/sign-in failed/i)).toBeInTheDocument()
      expect(screen.getByText('Authelia session not found')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /back to sign in/i }))
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true })
  })
})
