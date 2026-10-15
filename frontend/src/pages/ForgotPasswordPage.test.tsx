import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ForgotPasswordPage from './ForgotPasswordPage'

const mockRequest = vi.hoisted(() => vi.fn())

vi.mock('../services/auth', () => ({ requestPasswordReset: mockRequest }))

function setup() {
  render(
    <MemoryRouter>
      <ForgotPasswordPage />
    </MemoryRouter>
  )
}

beforeEach(() => vi.clearAllMocks())

describe('ForgotPasswordPage', () => {
  it('asks for the link and says what happens next, the same for any address', async () => {
    mockRequest.mockResolvedValue('If this address has an account, a link to reset the password is on its way.')
    setup()

    await userEvent.type(screen.getByLabelText('Email'), 'rider@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send the link' }))

    expect(mockRequest).toHaveBeenCalledWith('rider@example.com')
    expect(await screen.findByRole('status')).toHaveTextContent(/If this address has an account/)
  })

  it('says whom to ask when this server sends no mail', async () => {
    mockRequest.mockRejectedValue(Object.assign(new Error('not available'), { status: 503 }))
    setup()

    await userEvent.type(screen.getByLabelText('Email'), 'rider@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send the link' }))

    expect(await screen.findByText(/Ask the person who invited you/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send the link' })).not.toBeInTheDocument()
  })

  it('shows any other failure and lets the athlete try again', async () => {
    mockRequest.mockRejectedValue(Object.assign(new Error('Too many password reset requests.'), { status: 429 }))
    setup()

    await userEvent.type(screen.getByLabelText('Email'), 'rider@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send the link' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Too many password reset requests.')
    expect(screen.getByRole('button', { name: 'Send the link' })).toBeEnabled()
  })
})
