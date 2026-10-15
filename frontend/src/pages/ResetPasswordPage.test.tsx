import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ResetPasswordPage from './ResetPasswordPage'

const mockConfirm = vi.hoisted(() => vi.fn())

vi.mock('../services/auth', () => ({ confirmPasswordReset: mockConfirm }))

const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG'

function setup(hash = `#token=${TOKEN}`) {
  window.history.replaceState(null, '', `/reset-password${hash}`)
  render(
    <MemoryRouter>
      <ResetPasswordPage />
    </MemoryRouter>
  )
}

async function fillIn(password: string, confirm = password) {
  await userEvent.type(screen.getByLabelText('New password'), password)
  await userEvent.type(screen.getByLabelText('Confirm new password'), confirm)
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => window.history.replaceState(null, '', '/'))

describe('ResetPasswordPage', () => {
  it('sets the new password with the token from the link, and takes the token out of the address bar', async () => {
    mockConfirm.mockResolvedValue(undefined)
    setup()

    expect(window.location.hash).toBe('')
    await fillIn('gravel climbs at dawn')
    await userEvent.click(screen.getByRole('button', { name: 'Set the new password' }))

    expect(mockConfirm).toHaveBeenCalledWith(TOKEN, 'gravel climbs at dawn')
    expect(await screen.findByRole('status')).toHaveTextContent(/every session is signed out/)
  })

  it('offers no submit until the password meets the rules and both fields match', async () => {
    setup()

    await fillIn('short', 'short')
    expect(screen.getByRole('button', { name: 'Set the new password' })).toBeDisabled()

    await userEvent.clear(screen.getByLabelText('New password'))
    await userEvent.clear(screen.getByLabelText('Confirm new password'))
    await fillIn('gravel climbs at dawn', 'gravel climbs at dusk')
    expect(screen.getByText('The two passwords do not match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Set the new password' })).toBeDisabled()
  })

  it('says when the link is spent and offers a new one', async () => {
    mockConfirm.mockRejectedValue(new Error('This link is no longer valid. Ask for a new one.'))
    setup()

    await fillIn('gravel climbs at dawn')
    await userEvent.click(screen.getByRole('button', { name: 'Set the new password' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('no longer valid')
    expect(screen.getByRole('link', { name: 'Ask for a new link' })).toHaveAttribute('href', '/forgot-password')
  })

  it('explains a link that arrived without its token', () => {
    setup('')

    expect(screen.getByText(/This link is incomplete/)).toBeInTheDocument()
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
  })
})
