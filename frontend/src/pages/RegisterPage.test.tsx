import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RegisterPage from './RegisterPage'
import { useAppStore } from '../store/useAppStore'

const { mockRegister, mockLoadUserData } = vi.hoisted(() => ({
  mockRegister: vi.fn(),
  mockLoadUserData: vi.fn(),
}))

vi.mock('../services/auth', () => ({ register: mockRegister }))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { mutations: { retry: false } } })
}

function setup() {
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockLoadUserData.mockResolvedValue(undefined)
})

describe('RegisterPage', () => {
  it('renders the registration form', () => {
    setup()
    expect(screen.getByRole('heading', { name: /create account/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Your name')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/strong password/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/repeat your password/i)).toBeInTheDocument()
  })

  it('navigates to /onboarding after successful registration with a token', async () => {
    mockRegister.mockResolvedValue('new-jwt-token')
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith('Alice', 'alice@example.com', 'Secur3!Pass')
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding')
    })
  })

  it('navigates to /login when registration returns no token (Authelia mode)', async () => {
    mockRegister.mockResolvedValue(null)
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Bob')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'bob@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/login')
    })
  })

  it('shows an error message when registration fails', async () => {
    mockRegister.mockRejectedValue(new Error('Email already in use'))
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(screen.getByText('Email already in use')).toBeInTheDocument()
    })
  })

  it('shows password strength guidance and blocks weak passwords', async () => {
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'password')

    expect(screen.getByText('One uppercase letter')).toBeInTheDocument()
    expect(screen.getByText('One number')).toBeInTheDocument()
    expect(screen.getByText('One special character')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create account/i })).toBeDisabled()
    expect(mockRegister).not.toHaveBeenCalled()
  })

  it('shows a clear error when confirmation does not match', async () => {
    setup()

    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Different1!')

    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create account/i })).toBeDisabled()
  })

  it('reveals passwords only while the eye buttons are pressed', async () => {
    setup()

    const passwordInput = screen.getByPlaceholderText(/strong password/i)
    const confirmInput = screen.getByPlaceholderText(/repeat your password/i)
    const passwordReveal = screen.getByRole('button', { name: /hold to show password/i })
    const confirmReveal = screen.getByRole('button', { name: /hold to show confirmation password/i })

    expect(passwordInput).toHaveAttribute('type', 'password')
    expect(confirmInput).toHaveAttribute('type', 'password')

    fireEvent.pointerDown(passwordReveal)
    expect(passwordInput).toHaveAttribute('type', 'text')
    fireEvent.pointerUp(passwordReveal)
    expect(passwordInput).toHaveAttribute('type', 'password')

    fireEvent.pointerDown(confirmReveal)
    expect(confirmInput).toHaveAttribute('type', 'text')
    fireEvent.pointerLeave(confirmReveal)
    expect(confirmInput).toHaveAttribute('type', 'password')
  })

  it('links to the login page', () => {
    setup()
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login')
  })
})
