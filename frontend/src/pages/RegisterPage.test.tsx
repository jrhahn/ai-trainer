import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RegisterPage from './RegisterPage'
import { resetInviteOnlyCache } from '../hooks/useInviteOnly'
import { useAppStore } from '../store/useAppStore'

const { mockRegister, mockLoadUserData, mockSignupConfig } = vi.hoisted(() => ({
  mockRegister: vi.fn(),
  mockLoadUserData: vi.fn(),
  mockSignupConfig: vi.fn(),
}))

vi.mock('../services/auth', () => ({
  register: mockRegister,
  fetchSignupConfig: mockSignupConfig,
  endSession: vi.fn(),
  CONFIRM_EMAIL: 'confirm-email',
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { mutations: { retry: false } } })
}

function setup(path = '/register') {
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <RegisterPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockLoadUserData.mockResolvedValue(undefined)
  mockSignupConfig.mockResolvedValue({ inviteOnly: false })
  resetInviteOnlyCache()
})

async function fillIn() {
  await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
  await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
  await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
  await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
  await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
}

describe('consent to the privacy policy (ai-trainer-ops#2)', { timeout: 15_000 }, () => {
  it('cannot be sent without it, and links the policy', async () => {
    setup()
    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')

    expect(screen.getByRole('button', { name: /create account/i })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Read the privacy policy' })).toHaveAttribute('href', '/privacy')
    await userEvent.click(screen.getByRole('checkbox', { name: /health data/i }))
    expect(screen.getByRole('button', { name: /create account/i })).toBeEnabled()
  })
})

describe('a closed round signs up by invitation (ops#108, #96)', { timeout: 15_000 }, () => {
  it('takes the code from the invitation link and sends it', async () => {
    mockSignupConfig.mockResolvedValue({ inviteOnly: true })
    mockRegister.mockResolvedValue('new-jwt-token')
    setup('/register?invite=K7QM-3XRP-WD')

    expect(await screen.findByText('Closed test · by invitation only')).toBeInTheDocument()
    expect(screen.getByLabelText('Invitation code')).toHaveValue('K7QM-3XRP-WD')
    await fillIn()
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() =>
      expect(mockRegister).toHaveBeenCalledWith('Alice', 'alice@example.com', 'Secur3!Pass', 'K7QM-3XRP-WD', true),
    )
  })

  it('cannot be sent without a code', async () => {
    mockSignupConfig.mockResolvedValue({ inviteOnly: true })
    setup()

    const field = await screen.findByLabelText('Invitation code')
    await fillIn()
    expect(screen.getByRole('button', { name: /create account/i })).toBeDisabled()
    await userEvent.type(field, 'abcd efgh jk')
    expect(screen.getByRole('button', { name: /create account/i })).toBeEnabled()
  })

  it('shows no code field on an open server, and keeps the old line', async () => {
    setup()

    await waitFor(() => expect(mockSignupConfig).toHaveBeenCalled())
    expect(screen.queryByLabelText('Invitation code')).not.toBeInTheDocument()
    expect(screen.getByText('Free · You bring a Gemini key')).toBeInTheDocument()
  })

  it('treats a server it cannot ask as open', async () => {
    mockSignupConfig.mockRejectedValue(new Error('offline'))
    setup()

    await waitFor(() => expect(mockSignupConfig).toHaveBeenCalled())
    expect(screen.queryByLabelText('Invitation code')).not.toBeInTheDocument()
  })
})

describe('RegisterPage', { timeout: 15_000 }, () => {
  it('renders the registration form', () => {
    setup()
    expect(screen.getByRole('heading', { name: /create account/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Your name')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/strong password/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/repeat your password/i)).toBeInTheDocument()
  })

  it('ties each label to its field, so a tap on it focuses the field (ai-trainer-ops#223)', () => {
    setup()
    expect(screen.getByLabelText('Name')).toBe(screen.getByPlaceholderText('Your name'))
    expect(screen.getByLabelText('Email')).toBe(screen.getByPlaceholderText('you@example.com'))
    expect(screen.getByLabelText('Password')).toBe(screen.getByPlaceholderText(/strong password/i))
    expect(screen.getByLabelText('Confirm password')).toBe(screen.getByPlaceholderText(/repeat your password/i))
  })

  it('navigates to /onboarding after successful registration with a token', async () => {
    mockRegister.mockResolvedValue('new-jwt-token')
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith('Alice', 'alice@example.com', 'Secur3!Pass', '', true)
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
    await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/login')
    })
  })

  it('sends a new account that must confirm its address to the sign-in page with a note', async () => {
    mockRegister.mockResolvedValue('confirm-email')
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Bob')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'bob@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/login', { state: { confirmEmail: 'bob@example.com' } })
    })
    expect(mockLoadUserData).not.toHaveBeenCalled()
  })

  it('shows an error message when registration fails', async () => {
    mockRegister.mockRejectedValue(new Error('Email already in use'))
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(screen.getByText('Email already in use')).toBeInTheDocument()
    })
  })

  it('says registration failed when the error carries no message', async () => {
    mockRegister.mockRejectedValue('nope')
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'Secur3!Pass')
    await userEvent.type(screen.getByPlaceholderText(/repeat your password/i), 'Secur3!Pass')
    await userEvent.click(screen.getByRole('checkbox', { name: /privacy policy/i }))
    await userEvent.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByText('Registration failed')).toBeInTheDocument()
  })

  it('shows password strength guidance and blocks weak passwords', async () => {
    setup()

    await userEvent.type(screen.getByPlaceholderText('Your name'), 'Alice')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'password')

    expect(screen.getByText('At least 10 characters (a short phrase works)')).toBeInTheDocument()
    expect(screen.queryByText('One uppercase letter')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create account/i })).toBeDisabled()
    expect(mockRegister).not.toHaveBeenCalled()
  })

  it('marks a long run of a few repeated characters as too simple', async () => {
    setup()

    await userEvent.type(screen.getByPlaceholderText(/strong password/i), 'abababababab')

    expect(screen.getByText('At least 10 characters (a short phrase works)').parentElement).toHaveClass('text-green-700')
    expect(screen.getByText('Not just a few repeated characters').parentElement).toHaveClass('text-gray-500')
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
