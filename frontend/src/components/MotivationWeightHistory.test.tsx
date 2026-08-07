import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MotivationWeightHistory from './MotivationWeightHistory'
import type { MotivationWeightEvent } from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())

vi.mock('../services/user', () => ({ fetchMotivationWeightHistory: mockFetch }))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

const LABELS = {
  enjoyment: 'Enjoyment',
  adaptation: 'Fitness gains',
  consistency: 'Consistency',
}

function makeEvent(overrides: Partial<MotivationWeightEvent> = {}): MotivationWeightEvent {
  return {
    id: 'evt-1',
    source: 'inferred',
    weightsBefore: { enjoyment: 0.25, adaptation: 0.3 },
    weightsAfter: { enjoyment: 0.28, adaptation: 0.28 },
    deltas: { enjoyment: 0.03, adaptation: -0.02 },
    rules: [
      {
        rule: 'modality_swap',
        signal: 'rode a different discipline than the one prescribed',
        observed: 1,
        strength: 1,
        effects: { enjoyment: 0.03, adaptation: -0.02 },
      },
    ],
    evidence: { rides: 12, modality_swaps: 12 },
    pinned: [],
    recordedAt: '2026-08-13T09:00:00Z',
    ...overrides,
  }
}

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MotivationWeightHistory componentLabels={LABELS} />
    </QueryClientProvider>
  )
}

async function open() {
  await userEvent.click(screen.getByRole('button', { name: /What moved this/ }))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetch.mockResolvedValue([makeEvent()])
})

describe('MotivationWeightHistory', () => {
  it('does not fetch until it is opened', () => {
    renderHistory()

    // Occasional question, not something the settings page owes on every load.
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('names which weights moved and by how much', async () => {
    renderHistory()
    await open()

    await waitFor(() => {
      expect(screen.getByText('Enjoyment')).toBeInTheDocument()
    })
    expect(screen.getByText('+3%')).toBeInTheDocument()
    expect(screen.getByText('−2%')).toBeInTheDocument()
  })

  it('gives the reason in the athlete’s own terms', async () => {
    renderHistory()
    await open()

    // The whole point of the trail: which evidence moved which weight.
    await waitFor(() => {
      expect(
        screen.getByText(
          /because you rode a different discipline than the one prescribed/
        )
      ).toBeInTheDocument()
    })
  })

  it('distinguishes the athlete’s own edit from a learning run', async () => {
    mockFetch.mockResolvedValue([
      makeEvent({ source: 'user_set', rules: null, evidence: null }),
    ])
    renderHistory()
    await open()

    await waitFor(() => {
      expect(screen.getByText('You changed this')).toBeInTheDocument()
    })
    expect(screen.queryByText(/because you/)).not.toBeInTheDocument()
  })

  it('says when a pinned weight refused a rule that argued for it', async () => {
    // A rule firing while nothing moves is the most confusing thing this screen
    // can show, so it is called out rather than left as a silent no-op.
    mockFetch.mockResolvedValue([
      makeEvent({ pinned: ['enjoyment'], deltas: { adaptation: -0.02 } }),
    ])
    renderHistory()
    await open()

    await waitFor(() => {
      expect(
        screen.getByText('Enjoyment stayed where you pinned it.')
      ).toBeInTheDocument()
    })
  })

  it('explains an empty history instead of showing a blank panel', async () => {
    mockFetch.mockResolvedValue([])
    renderHistory()
    await open()

    await waitFor(() => {
      expect(screen.getByText(/Nothing has moved your balance yet/)).toBeInTheDocument()
    })
  })

  it('reports a failed load', async () => {
    mockFetch.mockRejectedValue(new Error('boom'))
    renderHistory()
    await open()

    await waitFor(() => {
      expect(screen.getByText('Could not load the history.')).toBeInTheDocument()
    })
  })
})
