import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminPage from './AdminPage'
import { useAppStore } from '../store/useAppStore'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({
  apiFetch: mockApiFetch,
  API_BASE: 'http://api.test/api/v1',
  setCsrfToken: vi.fn(),
}))

const user = {
  id: 'u1',
  email: 'rider@example.com',
  name: 'Rider One',
  createdAt: '2024-01-01T00:00:00Z',
  lastLogin: '2024-05-01T00:00:00Z',
  aiProvider: 'openai',
  isOnboarded: true,
  stravaConnected: true,
  stravaAnalysisComplete: true,
  consumedTokens: 12345,
  rideCount: 42,
  lastActivityDate: '2024-05-01',
  chatMessageCount: 7,
}

const statsResponse = { users: [user], totalUsers: 1, totalTokens: 12345 }

type Call = { method?: string; token?: string | null; body?: unknown }

/** The admin API as `apiFetch` answers it: `failures` maps "METHOD /path" to the
 *  server's words for a failed call (ai-trainer-ops#276: no raw fetch left). */
function adminApi({
  totpRequired = false,
  stats = statsResponse as unknown,
  failures = {} as Record<string, string>,
} = {}) {
  return async (path: string, opts: Call = {}) => {
    const key = `${opts.method ?? 'GET'} ${path}`
    if (key in failures) throw Object.assign(new Error(failures[key]), { status: 400 })
    if (key === 'GET /admin/totp-required') return { required: totpRequired }
    if (key === 'POST /admin/login') return { access_token: 'admin-tok' }
    if (key === 'GET /admin/invite-codes') return { codes: [], inviteOnly: true }
    if (opts.method === 'DELETE') return undefined
    if (path.endsWith('/revoke-sessions')) return { tokenGeneration: 1 }
    return stats
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  // The invitations section asks for its own list (ops#108, #96).
  mockApiFetch.mockImplementation(adminApi())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function login() {
  await userEvent.type(screen.getByPlaceholderText('Admin password'), 'secret')
  await userEvent.click(screen.getByRole('button', { name: 'Sign In' }))
  await screen.findByRole('heading', { name: 'Admin Panel' })
}

describe('AdminPage', () => {
  it('shows the login screen first', () => {
    render(<AdminPage />)
    expect(screen.getByPlaceholderText('Admin password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument()
  })

  it('logs in and renders the stats dashboard', async () => {
    render(<AdminPage />)
    await login()

    expect(mockApiFetch).toHaveBeenCalledWith('/admin/login', expect.objectContaining({ method: 'POST' }))
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/users', { token: 'admin-tok' })
    expect(screen.getByText('Total users')).toBeInTheDocument()
    expect(screen.getByText('Total tokens')).toBeInTheDocument()
    expect(screen.getByText('Rider One')).toBeInTheDocument()
    expect(screen.getByText('rider@example.com')).toBeInTheDocument()
    // "12,345" appears in both the tokens stat card and the user's row
    expect(screen.getAllByText('12,345').length).toBeGreaterThan(0)
  })

  it('shows an error when login fails', async () => {
    mockApiFetch.mockImplementation(adminApi({ failures: { 'POST /admin/login': 'Wrong password' } }))
    render(<AdminPage />)
    await userEvent.type(screen.getByPlaceholderText('Admin password'), 'bad')
    await userEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(await screen.findByText('Wrong password')).toBeInTheDocument()
  })

  it('signs out back to the login screen', async () => {
    render(<AdminPage />)
    await login()
    await userEvent.click(screen.getByRole('button', { name: /Sign out/ }))
    expect(screen.getByPlaceholderText('Admin password')).toBeInTheDocument()
  })

  it('toggles the sort indicator when a column header is clicked', async () => {
    render(<AdminPage />)
    await login()
    fireEvent.click(screen.getByRole('columnheader', { name: /Rides/ }))
    expect(screen.getByRole('columnheader', { name: /Rides/ }).textContent).toContain('▼')
    fireEvent.click(screen.getByRole('columnheader', { name: /Rides/ }))
    expect(screen.getByRole('columnheader', { name: /Rides/ }).textContent).toContain('▲')
  })

  it('requires the exact confirmation phrase before deleting a user', async () => {
    render(<AdminPage />)
    await login()

    await userEvent.click(screen.getByRole('button', { name: 'Delete user' }))
    const deleteBtn = screen.getByRole('button', { name: 'Delete permanently' })
    expect(deleteBtn).toBeDisabled()

    await userEvent.type(
      screen.getByPlaceholderText('delete rider@example.com'),
      'delete rider@example.com'
    )
    expect(deleteBtn).toBeEnabled()

    await userEvent.click(deleteBtn)
    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith('/admin/users/u1', { token: 'admin-tok', method: 'DELETE' })
    )
    // stats reloaded after delete (the flag list loads once beside them, #94)
    expect(mockApiFetch.mock.calls.filter(([path]) => path === '/admin/users')).toHaveLength(2)
  })

  it('revokes an athlete’s sessions after a confirmation (#704)', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    render(<AdminPage />)
    await login()

    await userEvent.click(screen.getByRole('button', { name: 'Revoke all sessions' }))

    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith('/admin/users/u1/revoke-sessions', {
        token: 'admin-tok',
        method: 'POST',
      })
    )
  })

  it('sends nothing if the confirmation is dismissed', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    render(<AdminPage />)
    await login()

    await userEvent.click(screen.getByRole('button', { name: 'Revoke all sessions' }))

    expect(mockApiFetch).not.toHaveBeenCalledWith(expect.stringContaining('/revoke-sessions'), expect.anything())
  })

  it('surfaces a failed revocation instead of looking like it worked', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    mockApiFetch.mockImplementation(
      adminApi({ failures: { 'POST /admin/users/u1/revoke-sessions': 'Admin credentials changed. Sign in again.' } }),
    )
    render(<AdminPage />)
    await login()

    await userEvent.click(screen.getByRole('button', { name: 'Revoke all sessions' }))

    expect(
      await screen.findByText('Admin credentials changed. Sign in again.')
    ).toBeInTheDocument()
  })

  it('says why a deletion failed and keeps the dialog open', async () => {
    mockApiFetch.mockImplementation(adminApi({ failures: { 'DELETE /admin/users/u1': 'User not found' } }))
    render(<AdminPage />)
    await login()

    await userEvent.click(screen.getByRole('button', { name: 'Delete user' }))
    await userEvent.type(screen.getByPlaceholderText('delete rider@example.com'), 'delete rider@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Delete permanently' }))

    expect(await screen.findByText('User not found')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Delete account permanently' })).toBeInTheDocument()
  })

  it('closes the delete modal on cancel', async () => {
    render(<AdminPage />)
    await login()
    await userEvent.click(screen.getByRole('button', { name: 'Delete user' }))
    expect(screen.getByText('Delete account permanently')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Delete account permanently')).not.toBeInTheDocument()
  })
})

describe('AdminPage — second factor (#688)', () => {
  it('does not ask for a code when the server has none configured', async () => {
    /*
     * Rendering the field unconditionally would fail a correct password on a
     * deployment without a second factor, which is worse than the one extra
     * request this check costs.
     */
    render(<AdminPage />)

    await waitFor(() => screen.getByPlaceholderText('Admin password'))
    expect(screen.queryByLabelText(/two-factor code/i)).not.toBeInTheDocument()
  })

  it('asks for a code and sends it when the server requires one', async () => {
    mockApiFetch.mockImplementation(adminApi({ totpRequired: true }))
    render(<AdminPage />)

    const codeField = await screen.findByLabelText(/two-factor code/i)
    await userEvent.type(screen.getByPlaceholderText('Admin password'), 'secret')
    await userEvent.type(codeField, '123456')
    await userEvent.click(screen.getByRole('button', { name: /sign in|log in|login/i }))

    await waitFor(() => {
      const loginCall = mockApiFetch.mock.calls.find(([path]) => path === '/admin/login')
      expect(loginCall).toBeDefined()
      expect(loginCall![1].body).toEqual({
        password: 'secret',
        code: '123456',
      })
    })
  })

  it('omits the code field entirely if the probe fails', async () => {
    /*
     * A failed probe leaves it off rather than on: the server enforces this,
     * and a hidden field cannot let a wrong login through — whereas a field
     * shown in error blocks a correct one.
     */
    mockApiFetch.mockImplementation(adminApi({ failures: { 'GET /admin/totp-required': 'offline' } }))
    render(<AdminPage />)

    await waitFor(() => screen.getByPlaceholderText('Admin password'))
    expect(screen.queryByLabelText(/two-factor code/i)).not.toBeInTheDocument()
  })
})

describe('AdminPage in German (ai-trainer-ops#104)', () => {
  it('names the panel, the columns and every age in German', async () => {
    const daysAgo = (days: number) => new Date(Date.now() - days * 86_400_000 - 60_000).toISOString()
    mockApiFetch.mockImplementation(adminApi({ stats: {
      users: [
        { ...user, id: 'a', email: 'a@example.com', createdAt: daysAgo(0), lastLogin: daysAgo(1) },
        { ...user, id: 'b', email: 'b@example.com', createdAt: daysAgo(5), lastLogin: daysAgo(60) },
        { ...user, id: 'c', email: 'c@example.com', createdAt: daysAgo(400), lastLogin: null, stravaConnected: false },
      ],
      totalUsers: 3,
      totalTokens: 1234567,
    } }))
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      render(<AdminPage />)
      await userEvent.type(screen.getByPlaceholderText('Admin-Passwort'), 'secret')
      await userEvent.click(screen.getByRole('button', { name: 'Anmelden' }))

      expect(await screen.findByRole('heading', { name: 'Admin-Bereich' })).toBeInTheDocument()
      expect(screen.getByText('1.234.567')).toBeInTheDocument()
      expect(screen.getByText('Letzte Anmeldung')).toBeInTheDocument()
      for (const age of ['heute', 'gestern', 'vor 5 T.', 'vor 2 Mon.', 'vor 1 J.']) {
        expect(screen.getByText(age)).toBeInTheDocument()
      }
      expect(screen.getByText('keine')).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})
