import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminPage from './AdminPage'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({ apiFetch: mockApiFetch, API_BASE: 'http://api.test/api/v1' }))

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

function mockFetch() {
  return vi.fn(async (url: string, opts?: RequestInit) => {
    if (url.endsWith('/admin/login')) {
      return { ok: true, json: async () => ({ access_token: 'admin-tok' }) } as Response
    }
    if (opts?.method === 'DELETE') {
      return { ok: true, json: async () => ({}) } as Response
    }
    return { ok: false, json: async () => ({ detail: 'unexpected' }) } as Response
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApiFetch.mockResolvedValue(statsResponse)
  vi.stubGlobal('fetch', mockFetch())
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

    expect(fetch).toHaveBeenCalledWith(
      'http://api.test/api/v1/admin/login',
      expect.objectContaining({ method: 'POST' })
    )
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/users', { token: 'admin-tok' })
    expect(screen.getByText('Total users')).toBeInTheDocument()
    expect(screen.getByText('Total tokens')).toBeInTheDocument()
    expect(screen.getByText('Rider One')).toBeInTheDocument()
    expect(screen.getByText('rider@example.com')).toBeInTheDocument()
    // "12,345" appears in both the tokens stat card and the user's row
    expect(screen.getAllByText('12,345').length).toBeGreaterThan(0)
  })

  it('shows an error when login fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, json: async () => ({ detail: 'Wrong password' }) }) as Response)
    )
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
      expect(fetch).toHaveBeenCalledWith(
        'http://api.test/api/v1/admin/users/u1',
        expect.objectContaining({ method: 'DELETE' })
      )
    )
    // stats reloaded after delete
    expect(mockApiFetch).toHaveBeenCalledTimes(2)
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
