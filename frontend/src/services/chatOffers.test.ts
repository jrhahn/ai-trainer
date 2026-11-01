import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({
  API_BASE: '/api/v1',
  apiFetch: mockApiFetch,
  authHeaders: (token: string) => ({ Authorization: `Bearer ${token}` }),
}))

import { rememberOfferAnswer, resolveChatOfferRemote } from './chatOffers'
import { chatMessageFromServer, fetchChatHistory } from './user'

const raceOffer = { date: '2027-01-17', replaces: { date: '2026-12-30', kind: 'profile' as const, eventId: null } }
const raceProposal = { raceDate: '2027-05-13', raceDescription: 'Puig Major', calendarRaces: 1 }
const profileProposal = { weeklyHours: 6, ftp: null, standingDaySwap: false }

function stored(overrides: Record<string, unknown> = {}) {
  return {
    id: 'm1',
    role: 'assistant',
    content: 'I can move the race if you confirm.',
    timestamp: '2026-11-01T10:00:00Z',
    planUpdateCount: null,
    notes: [],
    daySummaries: [],
    offers: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ai-trainer-ops#338 T1: the buttons under a reply come back after a reload.
describe('the chat history brings the offers back', () => {
  it('puts an unanswered offer back on the reply, with its id', async () => {
    mockApiFetch.mockResolvedValue({
      messages: [
        stored({ id: 'u1', role: 'user', content: 'Das Rennen wird verschoben.' }),
        stored({ offers: { raceOffer, raceProposal, profileProposal, resolved: {} } }),
      ],
    })

    const [user, reply] = await fetchChatHistory('tok')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/chat', { token: 'tok' })
    expect(user.id).toBe('u1')
    expect(user.raceOffer).toBeUndefined()
    expect(reply.id).toBe('m1')
    expect(reply.raceOffer).toEqual(raceOffer)
    expect(reply.raceProposal).toEqual(raceProposal)
    expect(reply.profileProposal).toEqual(profileProposal)
    expect(reply).not.toHaveProperty('offers')
  })

  it('leaves out an offer the athlete already answered', () => {
    const reply = chatMessageFromServer(
      stored({
        offers: {
          raceOffer,
          raceProposal,
          profileProposal,
          resolved: { raceOffer: 'accepted', profileProposal: 'dismissed' },
        },
      }),
    )

    expect(reply.raceOffer).toBeUndefined()
    expect(reply.profileProposal).toBeUndefined()
    expect(reply.raceProposal).toEqual(raceProposal)
  })

  it('keeps a reply without offers as it was', () => {
    const reply = chatMessageFromServer(stored({ id: null, planUpdateCount: 2 }))

    expect(reply).toEqual({
      role: 'assistant',
      content: 'I can move the race if you confirm.',
      timestamp: '2026-11-01T10:00:00Z',
      planUpdateCount: 2,
      notes: [],
      daySummaries: [],
    })
  })
})

describe('resolveChatOfferRemote', () => {
  it('posts the answer for that offer of that reply', async () => {
    mockApiFetch.mockResolvedValue({})

    await resolveChatOfferRemote('tok', 'm 1', 'profileProposal', 'dismissed')

    expect(mockApiFetch).toHaveBeenCalledWith('/users/me/chat/m%201/offers/profileProposal/resolve', {
      token: 'tok',
      method: 'POST',
      body: { outcome: 'dismissed' },
    })
  })
})

describe('rememberOfferAnswer', () => {
  it('sends nothing without a login or a stored reply', () => {
    rememberOfferAnswer(null, 'm1', 'raceOffer', 'accepted')
    rememberOfferAnswer('tok', undefined, 'raceOffer', 'accepted')

    expect(mockApiFetch).not.toHaveBeenCalled()
  })

  it('swallows a failure: the answer itself is done already', async () => {
    mockApiFetch.mockRejectedValue(new Error('offline'))

    expect(() => rememberOfferAnswer('tok', 'm1', 'raceOffer', 'accepted')).not.toThrow()
    await Promise.resolve()
    expect(mockApiFetch).toHaveBeenCalledTimes(1)
  })
})
