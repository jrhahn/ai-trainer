import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SessionSettings from './SessionSettings'

const { mockRevokeAll, mockLogout } = vi.hoisted(() => ({
  mockRevokeAll: vi.fn(),
  mockLogout: vi.fn(),
}))

vi.mock('../services/sessions', () => ({
  revokeAllSessions: mockRevokeAll,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (
    selector: (state: { authToken: string; logout: () => void }) => unknown
  ) => selector({ authToken: 'test-token', logout: mockLogout }),
}))

function renderComponent() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <SessionSettings />
    </QueryClientProvider>
  )
}

describe('SessionSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRevokeAll.mockResolvedValue({ tokenGeneration: 1 })
  })

  it('does not ask for a password until the action is chosen', () => {
    renderComponent()

    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Confirm your password')).not.toBeInTheDocument()
  })

  it('sends the password and signs this browser out on success', async () => {
    const user = userEvent.setup()
    renderComponent()

    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    await user.type(screen.getByLabelText('Confirm your password'), 'Str0ng!Pass')
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))

    await waitFor(() => {
      expect(mockRevokeAll).toHaveBeenCalledWith('test-token', 'Str0ng!Pass')
    })
    // The request really does invalidate the token it was made with, so
    // staying on the page would leave a session the server has already
    // refused — every subsequent call would 401.
    await waitFor(() => expect(mockLogout).toHaveBeenCalled())
  })

  it('warns that this browser goes too, before anything is sent', async () => {
    const user = userEvent.setup()
    renderComponent()

    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))

    expect(screen.getByText(/signed out here as well/i)).toBeInTheDocument()
    expect(mockRevokeAll).not.toHaveBeenCalled()
  })

  it('cannot be submitted without a password', async () => {
    const user = userEvent.setup()
    renderComponent()

    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))

    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toBeDisabled()
  })

  it('keeps the session when the password is wrong', async () => {
    const user = userEvent.setup()
    mockRevokeAll.mockRejectedValue(new Error('Incorrect password.'))
    renderComponent()

    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    await user.type(screen.getByLabelText('Confirm your password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))

    expect(await screen.findByText('Incorrect password.')).toBeInTheDocument()
    // Logging out on a failed attempt would turn a typo into a sign-out, and
    // hide from the athlete that nothing was actually revoked.
    expect(mockLogout).not.toHaveBeenCalled()
  })

  it('cancelling clears the password it was given', async () => {
    const user = userEvent.setup()
    renderComponent()

    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    await user.type(screen.getByLabelText('Confirm your password'), 'typed-then-cancelled')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))

    expect(screen.getByLabelText('Confirm your password')).toHaveValue('')
  })
})
