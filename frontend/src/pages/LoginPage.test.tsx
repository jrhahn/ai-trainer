import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import LoginPage from './LoginPage'
import { useAppStore } from '../store/useAppStore'

const { mockLogin, mockLoginStep, mockLoginWithTotp, mockLoadUserData } = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockLoginStep: vi.fn(),
  mockLoginWithTotp: vi.fn(),
  mockLoadUserData: vi.fn(),
}))

vi.mock('../services/auth', () => ({
  login: mockLogin,
  loginStep: mockLoginStep,
  loginWithTotp: mockLoginWithTotp,
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { mutations: { retry: false } } })
}

function setup() {
  useAppStore.setState({ authToken: null })
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockLoadUserData.mockResolvedValue(undefined)
})

describe('LoginPage', () => {
  it('renders the sign-in form', () => {
    setup()
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Your password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('navigates to / after a successful login', async () => {
    mockLoginStep.mockResolvedValue({ kind: 'token', token: 'jwt-token' })
    setup()

    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText('Your password'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(mockLoginStep).toHaveBeenCalledWith('alice@example.com', 'password123')
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })
  })

  it('shows an error message when login fails', async () => {
    mockLoginStep.mockRejectedValue(new Error('Invalid credentials'))
    setup()

    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'bad@example.com')
    await userEvent.type(screen.getByPlaceholderText('Your password'), 'wrongpass')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByText('Invalid credentials')).toBeInTheDocument()
    })
  })

  it('links to the register page', () => {
    setup()
    expect(screen.getByRole('link', { name: /register/i })).toHaveAttribute('href', '/register')
  })
})

describe('LoginPage — second factor (#688)', () => {
  async function signInWithPassword() {
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText('Your password'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
  }

  it('asks for a code instead of signing in when the server returns a challenge', async () => {
    mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
    setup()

    await signInWithPassword()

    await waitFor(() => {
      expect(screen.getByPlaceholderText('000000')).toBeInTheDocument()
    })
    // The password alone must not have got anybody in.
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('exchanges the code for a token and navigates', async () => {
    mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
    mockLoginWithTotp.mockResolvedValue('jwt-token')
    setup()

    await signInWithPassword()
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '123456')
    await userEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => {
      expect(mockLoginWithTotp).toHaveBeenCalledWith('chal-1', '123456', false)
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })
  })

  it('passes the trust-this-device choice through', async () => {
    mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
    mockLoginWithTotp.mockResolvedValue('jwt-token')
    setup()

    await signInWithPassword()
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '123456')
    await userEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => {
      expect(mockLoginWithTotp).toHaveBeenCalledWith('chal-1', '123456', true)
    })
  })

  it('clears the code after a rejected attempt', async () => {
    /*
     * The challenge is single-use server-side, so a failed attempt has spent
     * it. Leaving the digits in place invites retyping into something that can
     * no longer succeed.
     */
    mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
    mockLoginWithTotp.mockRejectedValue(new Error('That code is not valid.'))
    setup()

    await signInWithPassword()
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '000000')
    await userEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => {
      expect(screen.getByText(/that code is not valid/i)).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText('000000')).toHaveValue('')
  })
})
