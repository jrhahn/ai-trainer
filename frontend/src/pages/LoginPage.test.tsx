import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import LoginPage from './LoginPage'
import { useAppStore } from '../store/useAppStore'

const { mockLogin, mockLoginStep, mockLoginWithTotp, mockLoadUserData, mockResend } = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockLoginStep: vi.fn(),
  mockLoginWithTotp: vi.fn(),
  mockLoadUserData: vi.fn(),
  mockResend: vi.fn(),
}))

vi.mock('../services/auth', () => ({
  login: mockLogin,
  loginStep: mockLoginStep,
  loginWithTotp: mockLoginWithTotp,
  resendEmailVerification: mockResend,
  endSession: vi.fn(),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { mutations: { retry: false } } })
}

function setup(state?: object) {
  useAppStore.setState({ authToken: null })
  useAppStore.getState().loadUserData = mockLoadUserData
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
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
  it('links the legal notice and the privacy policy, signed out (ai-trainer-ops#1)', () => {
    setup()
    const legal = screen.getByRole('navigation', { name: 'Legal' })
    expect(within(legal).getByRole('link', { name: 'Legal notice' })).toHaveAttribute('href', '/impressum')
    expect(within(legal).getByRole('link', { name: 'Privacy policy' })).toHaveAttribute('href', '/privacy')
  })

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

  it('says where the confirmation link went when a new account arrives here', () => {
    setup({ confirmEmail: 'new@example.com' })

    expect(screen.getByRole('status')).toHaveTextContent('We sent a link to new@example.com')
    // The subject the mail goes out with, so it can be found (ai-trainer-ops#220).
    expect(screen.getByRole('status')).toHaveTextContent('subject "Confirm your Train Like a Pro account"')
    expect(screen.getByRole('status')).toHaveTextContent('Look in the spam folder.')
    expect(screen.getByPlaceholderText('you@example.com')).toHaveValue('new@example.com')
  })

  it('offers a new link when the address is not confirmed yet (app #687)', async () => {
    mockLoginStep.mockRejectedValue(
      Object.assign(new Error('Confirm your email address first.'), { status: 403 }),
    )
    mockResend.mockResolvedValue('A new link is on its way.')
    setup()

    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'new@example.com')
    await userEvent.type(screen.getByPlaceholderText('Your password'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await userEvent.click(await screen.findByRole('button', { name: 'Send the link again' }))

    expect(mockResend).toHaveBeenCalledWith('new@example.com')
    expect(await screen.findByText('A new link is on its way.')).toBeInTheDocument()
  })

  it('gives the resend reply in German (ai-trainer-ops#121)', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      mockLoginStep.mockRejectedValue(
        Object.assign(new Error('Confirm your email address first.'), { status: 403 }),
      )
      mockResend.mockResolvedValue('If this address has an account that is not confirmed yet, a new link is on its way.')
      setup()

      await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'new@example.com')
      await userEvent.type(screen.getByPlaceholderText('Dein Passwort'), 'password123')
      await userEvent.click(screen.getByRole('button', { name: /anmelden/i }))
      await userEvent.click(await screen.findByRole('button', { name: 'Link erneut senden' }))

      expect(
        await screen.findByText('Wenn zu dieser Adresse ein noch nicht bestätigtes Konto gehört, ist ein neuer Link unterwegs.')
      ).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
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

  it("shows the server's refusal in German when the interface is German (ai-trainer-ops#104)", async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      mockLoginStep.mockRejectedValue(new Error('Invalid email or password'))
      setup()

      await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'bad@example.com')
      await userEvent.type(screen.getByPlaceholderText('Dein Passwort'), 'wrongpass')
      await userEvent.click(screen.getByRole('button', { name: 'Anmelden' }))

      expect(await screen.findByText('E-Mail oder Passwort ungültig')).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })

  it("carries a rejected code's reason back in German", async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
      mockLoginWithTotp.mockRejectedValue(new Error('This sign-in challenge was already used.'))
      setup()

      await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'alice@example.com')
      await userEvent.type(screen.getByPlaceholderText('Dein Passwort'), 'password123')
      await userEvent.click(screen.getByRole('button', { name: 'Anmelden' }))
      await userEvent.type(await screen.findByPlaceholderText('000000'), '123456')
      await userEvent.click(screen.getByRole('button', { name: 'Prüfen' }))

      expect(
        await screen.findByText('Diese Anmeldeanfrage wurde schon benutzt. Bitte melde dich erneut an.'),
      ).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })

  it('links to the register page', () => {
    setup()
    expect(screen.getByRole('link', { name: /register/i })).toHaveAttribute('href', '/register')
  })

  it('leads a forgotten password to the reset page (ai-trainer-ops#35)', () => {
    setup()

    expect(screen.getByRole('link', { name: 'Forgot your password?' })).toHaveAttribute('href', '/forgot-password')
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

  it('returns to the password step after a rejected code, carrying the reason', async () => {
    /*
     * A challenge is single-use server-side, so a rejected code has spent it:
     * the field it came from can no longer succeed, and retrying there answers
     * "this challenge was already used", which explains nothing. Staying put
     * would be a dead end, so the form goes back and brings the reason along.
     */
    mockLoginStep.mockResolvedValue({ kind: 'mfa', challenge: 'chal-1' })
    mockLoginWithTotp.mockRejectedValue(new Error('That code is not valid.'))
    setup()

    await signInWithPassword()
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '000000')
    await userEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => {
      expect(screen.getByText(/that code is not valid.*sign in again/i)).toBeInTheDocument()
    })
    // Back on the password step: the spent code field is gone.
    expect(screen.queryByPlaceholderText('000000')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('Your password')).toBeInTheDocument()
  })

  it('clears a carried-over code error once the password is re-submitted', async () => {
    mockLoginStep
      .mockResolvedValueOnce({ kind: 'mfa', challenge: 'chal-1' })
      .mockResolvedValueOnce({ kind: 'mfa', challenge: 'chal-2' })
    mockLoginWithTotp.mockRejectedValue(new Error('That code is not valid.'))
    setup()

    await signInWithPassword()
    await waitFor(() => screen.getByPlaceholderText('000000'))
    await userEvent.type(screen.getByPlaceholderText('000000'), '000000')
    await userEvent.click(screen.getByRole('button', { name: /verify/i }))
    await waitFor(() => screen.getByText(/sign in again/i))

    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('000000')).toBeInTheDocument()
    })
    expect(screen.queryByText(/sign in again/i)).not.toBeInTheDocument()
  })
})
