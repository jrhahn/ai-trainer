import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminInviteCodes, { inviteLink } from './AdminInviteCodes'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({ apiFetch: mockApiFetch }))

const open = { id: 'a', code: 'K7QM-3XRP-WD', note: 'for Anna', createdAt: '2026-10-18T19:00:00Z', usedAt: null }
const used = { id: 'b', code: 'ZZZZ-YYYY-XX', note: null, createdAt: '2026-10-18T18:00:00Z', usedAt: '2026-10-18T20:00:00Z' }

beforeEach(() => {
  vi.clearAllMocks()
  mockApiFetch.mockResolvedValue({ codes: [open, used], inviteOnly: true })
})

describe('AdminInviteCodes (ops#108, #96)', () => {
  it('lists the invitations, used and open', async () => {
    render(<AdminInviteCodes token="admin" />)

    expect(await screen.findByText('K7QM-3XRP-WD')).toBeInTheDocument()
    expect(screen.getByText('for Anna')).toBeInTheDocument()
    expect(screen.getByText('Not used yet')).toBeInTheDocument()
    expect(screen.getByText(/^Used /)).toBeInTheDocument()
    // A used code can be neither copied nor withdrawn.
    expect(screen.getAllByRole('button', { name: /copy link/i })).toHaveLength(1)
    expect(screen.queryByText(/SIGNUP_INVITE_ONLY/)).not.toBeInTheDocument()
  })

  it('makes invitations with a label, and copies the link a friend opens', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<AdminInviteCodes token="admin" />)
    await screen.findByText('K7QM-3XRP-WD')

    await userEvent.type(screen.getByPlaceholderText('e.g. Anna'), 'Ben')
    await userEvent.click(screen.getByRole('button', { name: 'Create invitation' }))
    expect(mockApiFetch).toHaveBeenCalledWith('/admin/invite-codes', {
      token: 'admin',
      method: 'POST',
      body: { count: 1, note: 'Ben' },
    })

    await userEvent.click(screen.getByRole('button', { name: /copy link/i }))
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/register?invite=K7QM-3XRP-WD`)
    expect(await screen.findByText('Link copied')).toBeInTheDocument()
  })

  it('withdraws an open invitation', async () => {
    render(<AdminInviteCodes token="admin" />)
    await screen.findByText('K7QM-3XRP-WD')
    mockApiFetch.mockResolvedValueOnce(undefined)

    await userEvent.click(screen.getByRole('button', { name: 'Withdraw invitation K7QM-3XRP-WD' }))

    expect(mockApiFetch).toHaveBeenCalledWith('/admin/invite-codes/a', { token: 'admin', method: 'DELETE' })
    await waitFor(() => expect(screen.queryByText('K7QM-3XRP-WD')).not.toBeInTheDocument())
  })

  it('says when sign-up is open anyway, and shows a failure', async () => {
    mockApiFetch.mockResolvedValueOnce({ codes: [], inviteOnly: false })
    render(<AdminInviteCodes token="admin" />)

    expect(await screen.findByText(/Set SIGNUP_INVITE_ONLY/)).toBeInTheDocument()
    expect(screen.getByText('No invitations yet.')).toBeInTheDocument()
    mockApiFetch.mockRejectedValueOnce(new Error('Request failed'))
    await userEvent.click(screen.getByRole('button', { name: 'Create invitation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Request failed')
  })

  it('builds the link from the code', () => {
    expect(inviteLink('A B')).toBe(`${window.location.origin}/register?invite=A%20B`)
  })
})
