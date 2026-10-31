import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import VerifyEmailPage from './VerifyEmailPage'

const { mockVerify, mockResend } = vi.hoisted(() => ({ mockVerify: vi.fn(), mockResend: vi.fn() }))

vi.mock('../services/auth', () => ({ verifyEmail: mockVerify, resendEmailVerification: mockResend }))

const TOKEN = 'user-1.1900000000.abcdef0123456789'

function setup(hash = `#token=${TOKEN}`) {
  window.history.replaceState(null, '', `/verify-email${hash}`)
  render(
    <StrictMode>
      <MemoryRouter>
        <VerifyEmailPage />
      </MemoryRouter>
    </StrictMode>,
  )
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => window.history.replaceState(null, '', '/'))

describe('VerifyEmailPage', () => {
  it('confirms the address once with the token from the link, and takes the token out of the address bar', async () => {
    mockVerify.mockResolvedValue(undefined)
    setup()

    expect(await screen.findByRole('status')).toHaveTextContent('Your address is confirmed.')
    // The page says it is done rather than still asking (ai-trainer-ops#176).
    expect(screen.getByRole('heading', { name: 'Email confirmed' })).toBeInTheDocument()
    expect(screen.getByText('Address confirmed')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Confirm your email' })).not.toBeInTheDocument()
    expect(mockVerify).toHaveBeenCalledTimes(1)
    expect(mockVerify).toHaveBeenCalledWith(TOKEN)
    expect(window.location.hash).toBe('')
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login')
  })

  it('signs in as a new account, so the login page does not welcome it back (ai-trainer-ops#323)', async () => {
    mockVerify.mockResolvedValue(undefined)
    window.history.replaceState(null, '', `/verify-email#token=${TOKEN}`)
    function LoginState() {
      return <p>login state: {JSON.stringify(useLocation().state)}</p>
    }
    render(
      <MemoryRouter initialEntries={['/verify-email']}>
        <Routes>
          <Route path="/verify-email" element={<VerifyEmailPage />} />
          <Route path="/login" element={<LoginState />} />
        </Routes>
      </MemoryRouter>,
    )

    await userEvent.click(await screen.findByRole('link', { name: 'Sign in' }))

    expect(screen.getByText('login state: {"newAccount":true}')).toBeInTheDocument()
  })

  it('says a used or expired link is no longer valid and offers a new one', async () => {
    mockVerify.mockRejectedValue(new Error('This link is no longer valid. Ask for a new one.'))
    mockResend.mockResolvedValue('If this address has an account that is not confirmed yet, a new link is on its way.')
    setup()

    expect(await screen.findByRole('alert')).toHaveTextContent('no longer valid')
    expect(screen.getByRole('heading', { name: 'Confirm your email' })).toBeInTheDocument()
    expect(screen.getByText('Confirm your address')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Send a new link to'), 'rider@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    expect(mockResend).toHaveBeenCalledWith('rider@example.com')
    expect(await screen.findByRole('status')).toHaveTextContent('a new link is on its way')
  })

  it('does not call the server for a link without a token', () => {
    setup('')

    expect(screen.getByRole('alert')).toHaveTextContent('This link is incomplete.')
    expect(mockVerify).not.toHaveBeenCalled()
  })
})
