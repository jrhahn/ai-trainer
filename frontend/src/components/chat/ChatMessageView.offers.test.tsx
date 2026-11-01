import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ChatMessageView from './ChatMessageView'
import { useAppStore } from '../../store/useAppStore'
import { chatMessageFromServer } from '../../services/user'

const mockRemember = vi.hoisted(() => vi.fn())
vi.mock('../../services/chatOffers', () => ({ rememberOfferAnswer: mockRemember }))

function storedReply(resolved: Record<string, 'accepted' | 'dismissed'>) {
  return {
    id: 'm1',
    role: 'assistant',
    content: 'If you confirm, your race moves to 17 January.',
    timestamp: '2026-11-01T10:00:00Z',
    offers: {
      raceOffer: { date: '2027-01-17', replaces: { date: '2026-12-30', kind: 'profile' as const } },
      profileProposal: { weeklyHours: 6 },
      resolved,
    },
  }
}

function show(resolved: Record<string, 'accepted' | 'dismissed'>) {
  render(
    <ChatMessageView
      msg={chatMessageFromServer(storedReply(resolved))}
      lastFailedMessage={null}
      retryDisabled={false}
      onRetry={() => {}}
    />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok' })
})

// ai-trainer-ops#338 T1: after a reload the reply's buttons are there again.
describe('a reply from the history shows its open offers', () => {
  it('shows the buttons of the offers not yet answered', () => {
    show({})

    expect(screen.getByRole('button', { name: 'Move' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update profile' })).toBeInTheDocument()
  })

  it('leaves out an answered offer', () => {
    show({ raceOffer: 'dismissed' })

    expect(screen.queryByRole('button', { name: 'Move' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update profile' })).toBeInTheDocument()
  })

  it('answering an offer from the history records it for that reply', async () => {
    show({})

    await userEvent.click(screen.getByRole('button', { name: 'No thanks' }))

    expect(mockRemember).toHaveBeenCalledWith('tok', 'm1', 'raceOffer', 'dismissed')
  })
})
