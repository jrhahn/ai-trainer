import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import LoginPage from './LoginPage'
import { useAppStore } from '../store/useAppStore'

const { mockLogin, mockLoadUserData } = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockLoadUserData: vi.fn(),
}))

vi.mock('../services/auth', () => ({ login: mockLogin }))

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
    mockLogin.mockResolvedValue('jwt-token')
    setup()

    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText('Your password'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('alice@example.com', 'password123')
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })
  })

  it('shows an error message when login fails', async () => {
    mockLogin.mockRejectedValue(new Error('Invalid credentials'))
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
