import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
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

function setup() {
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <MemoryRouter>
      <RegisterPage />
    </MemoryRouter>
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
    expect(screen.getByPlaceholderText(/at least 8 characters/i)).toBeInTheDocument()
  })

  it('navigates to /onboarding after successful registration with a token', async () => {
    mockRegister.mockResolvedValue('new-jwt-token')
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/at least 8 characters/i), 'securepassword')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith('Alice', 'alice@example.com', 'securepassword')
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding')
    })
  })

  it('navigates to /login when registration returns no token (Authelia mode)', async () => {
    mockRegister.mockResolvedValue(null)
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Bob')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'bob@example.com')
    await userEvent.type(screen.getByPlaceholderText(/at least 8 characters/i), 'securepassword')
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
    await userEvent.type(screen.getByPlaceholderText(/at least 8 characters/i), 'securepassword')
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(screen.getByText('Email already in use')).toBeInTheDocument()
    })
  })

  it('links to the login page', () => {
    setup()
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login')
  })
})
