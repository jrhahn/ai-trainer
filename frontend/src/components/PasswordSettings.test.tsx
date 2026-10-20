import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import PasswordSettings from './PasswordSettings'

const { mockChange, mockLogout, mockEndSession } = vi.hoisted(() => ({
  mockChange: vi.fn(),
  mockLogout: vi.fn(),
  mockEndSession: vi.fn(),
}))

vi.mock('../services/auth', () => ({ changePassword: mockChange }))
vi.mock('../services/sessions', () => ({ endSession: mockEndSession }))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: unknown) => unknown) =>
    selector({
      authToken: 'test-token',
      logout: mockLogout,
      userProfile: { name: 'Jamie Rider', email: 'jamie@example.com' },
    }),
}))

const NEW = 'gravel climbs at dawn'

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <PasswordSettings />
    </QueryClientProvider>
  )
  return userEvent.setup()
}

async function fill(user: ReturnType<typeof userEvent.setup>, current: string, next: string, repeat = next) {
  await user.click(screen.getByRole('button', { name: 'Change password' }))
  await user.type(screen.getByLabelText('Current password'), current)
  await user.type(screen.getByLabelText('New password'), next)
  await user.type(screen.getByLabelText('Confirm new password'), repeat)
}

describe('PasswordSettings (ai-trainer-ops#172)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockChange.mockResolvedValue(undefined)
  })

  it('asks for nothing until the athlete chooses to change it', () => {
    renderCard()
    expect(screen.getByRole('heading', { name: 'Password' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
  })

  it('lets the password manager fill the right fields', async () => {
    const user = renderCard()
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByLabelText('Current password')).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByLabelText('New password')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByLabelText('Confirm new password')).toHaveAttribute('autocomplete', 'new-password')
  })

  it('sends both passwords, says every session ended, and signs this browser out on request', async () => {
    const user = renderCard()
    await fill(user, 'Str0ng!Pass', NEW)
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    await waitFor(() => expect(mockChange).toHaveBeenCalledWith('test-token', 'Str0ng!Pass', NEW))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Your password is changed, and every session is signed out.'
    )
    await user.click(screen.getByRole('button', { name: 'Sign in with the new password' }))
    expect(mockEndSession).toHaveBeenCalledWith(mockLogout)
  })

  it('holds back a new password the rules refuse, or one built from the name', async () => {
    const user = renderCard()
    await fill(user, 'Str0ng!Pass', 'short')
    expect(screen.getByRole('button', { name: 'Change password' })).toBeDisabled()

    await user.clear(screen.getByLabelText('New password'))
    await user.clear(screen.getByLabelText('Confirm new password'))
    await user.type(screen.getByLabelText('New password'), 'jamie rides far')
    await user.type(screen.getByLabelText('Confirm new password'), 'jamie rides far')
    expect(screen.getByText(/from your name or email/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toBeDisabled()
    expect(mockChange).not.toHaveBeenCalled()
  })

  it('says when the two new passwords differ', async () => {
    const user = renderCard()
    await fill(user, 'Str0ng!Pass', NEW, `${NEW}x`)
    expect(screen.getByText('The two new passwords differ.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toBeDisabled()
  })

  it('shows the server\'s answer to a wrong current password and stays open', async () => {
    mockChange.mockRejectedValueOnce(new Error('Incorrect password.'))
    const user = renderCard()
    await fill(user, 'wrong one', NEW)
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect password.')
    expect(screen.getByLabelText('Current password')).toHaveValue('wrong one')
    expect(mockEndSession).not.toHaveBeenCalled()
  })

  it('falls back to a generic message for a non-Error failure', async () => {
    mockChange.mockRejectedValueOnce('boom')
    const user = renderCard()
    await fill(user, 'Str0ng!Pass', NEW)
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The password could not be changed.')
  })

  it('cancel clears what was typed', async () => {
    const user = renderCard()
    await fill(user, 'Str0ng!Pass', NEW)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(screen.getByLabelText('New password')).toHaveValue('')
  })
})
