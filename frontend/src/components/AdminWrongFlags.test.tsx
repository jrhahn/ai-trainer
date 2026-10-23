import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdminWrongFlags from './AdminWrongFlags'

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }))
vi.mock('../services/api', () => ({ apiFetch: mockApiFetch }))

beforeEach(() => mockApiFetch.mockReset())

describe('AdminWrongFlags (ai-trainer-ops#94)', () => {
  it('lists each flag with who, what, the note and the commit', async () => {
    mockApiFetch.mockResolvedValue({
      flags: [
        {
          id: 'f1',
          createdAt: '2026-10-14T10:00:00Z',
          email: 'tester@example.com',
          target: 'day',
          ref: '2026-10-15#0',
          excerpt: 'Long ride\n4h',
          note: 'The race is the day after.',
          gitSha: 'abc1234def',
        },
      ],
    })
    render(<AdminWrongFlags token="admin" />)

    expect(await screen.findByText('The race is the day after.')).toBeInTheDocument()
    expect(screen.getByText(/tester@example\.com · planned day 2026-10-15#0 · abc1234$/)).toBeInTheDocument()
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/wrong-flags', { token: 'admin' })
  })

  it('says when there are none', async () => {
    mockApiFetch.mockResolvedValue({ flags: [] })
    render(<AdminWrongFlags token="admin" />)

    expect(await screen.findByText('No flags yet.')).toBeInTheDocument()
  })
})

describe('AdminWrongFlags — the rest (ai-trainer-ops#104)', () => {
  it('names a flag on a coach reply', async () => {
    mockApiFetch.mockResolvedValue({
      flags: [
        {
          id: 'f2',
          createdAt: '2026-10-14T10:00:00Z',
          email: 'tester@example.com',
          target: 'reply',
          ref: 'msg-9',
          excerpt: 'Ride four hours tomorrow.',
          note: null,
          gitSha: 'abc1234def',
        },
      ],
    })
    render(<AdminWrongFlags token="admin" />)

    expect(await screen.findByText(/coach reply msg-9/)).toBeInTheDocument()
  })
})

describe('AdminWrongFlags — handled (ai-trainer-ops#96)', () => {
  const open = {
    id: 'f3',
    createdAt: '2026-10-27T10:00:00Z',
    email: 'tester@example.com',
    target: 'day' as const,
    ref: '2026-10-28#0',
    excerpt: 'Long ride',
    note: null,
    gitSha: 'abc1234def',
    handledAt: null,
    handledNote: null,
  }
  const handled = { ...open, id: 'f4', handledAt: '2026-10-28T09:00:00Z', handledNote: 'ops#212' }

  it('counts the open flags and shows how a handled one was handled', async () => {
    mockApiFetch.mockResolvedValue({ flags: [open, handled] })
    render(<AdminWrongFlags token="admin" />)

    expect(await screen.findByText('1 of 2 still open')).toBeInTheDocument()
    expect(screen.getByText('· ops#212')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Mark handled' })).toHaveLength(1)
  })

  it('marks a flag handled with the note, and loads the list again', async () => {
    mockApiFetch.mockResolvedValueOnce({ flags: [open] })
    mockApiFetch.mockResolvedValueOnce(undefined)
    mockApiFetch.mockResolvedValueOnce({ flags: [{ ...open, handledAt: '2026-10-28T09:00:00Z', handledNote: 'answered' }] })
    render(<AdminWrongFlags token="admin" />)

    fireEvent.change(await screen.findByLabelText('How it was handled'), { target: { value: '  answered ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Mark handled' }))

    expect(await screen.findByText('0 of 1 still open')).toBeInTheDocument()
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/wrong-flags/f3/handled', {
      token: 'admin',
      method: 'PUT',
      body: { handled: true, note: 'answered' },
    })
    expect(mockApiFetch).toHaveBeenCalledTimes(3)
  })

  it('opens a handled flag again', async () => {
    mockApiFetch.mockResolvedValueOnce({ flags: [handled] })
    mockApiFetch.mockResolvedValueOnce(undefined)
    mockApiFetch.mockResolvedValueOnce({ flags: [{ ...handled, handledAt: null, handledNote: null }] })
    render(<AdminWrongFlags token="admin" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Open again' }))

    expect(await screen.findByText('1 of 1 still open')).toBeInTheDocument()
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/wrong-flags/f4/handled', {
      token: 'admin',
      method: 'PUT',
      body: { handled: false, note: null },
    })
  })

  it('says when the mark could not be saved', async () => {
    mockApiFetch.mockResolvedValueOnce({ flags: [open] })
    mockApiFetch.mockRejectedValueOnce(new Error('Flag not found'))
    render(<AdminWrongFlags token="admin" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Mark handled' }))

    expect(await screen.findByText('Flag not found')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark handled' })).toBeEnabled())
  })

  it('falls back to its own words for an error that is not an Error', async () => {
    mockApiFetch.mockResolvedValueOnce({ flags: [open] })
    mockApiFetch.mockRejectedValueOnce('nope')
    render(<AdminWrongFlags token="admin" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Mark handled' }))

    expect(await screen.findByText('Could not save that.')).toBeInTheDocument()
  })
})
