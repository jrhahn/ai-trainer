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

describe('ForgotPasswordPage in German (ai-trainer-ops#121)', () => {
  it('gives the server reply in German, not mixed into a German paragraph', async () => {
    const { useAppStore } = await import('../store/useAppStore')
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      mockRequest.mockResolvedValue(
        'If this address has an account, a link to reset the password is on its way. It works for 60 minutes.'
      )
      setup()

      await userEvent.type(screen.getByLabelText('E-Mail'), 'rider@example.com')
      await userEvent.click(screen.getByRole('button', { name: 'Link senden' }))

      const status = await screen.findByRole('status')
      expect(status).toHaveTextContent(/^Wenn zu dieser Adresse ein Konto gehört, .*Er gilt 60 Minuten\./)
      expect(status).not.toHaveTextContent(/If this address/)
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})
