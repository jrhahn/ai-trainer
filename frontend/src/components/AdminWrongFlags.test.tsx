import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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
