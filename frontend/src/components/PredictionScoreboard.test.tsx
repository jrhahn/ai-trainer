import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import PredictionScoreboard from './PredictionScoreboard'
import { useAppStore } from '../store/useAppStore'
import type { AthletePrediction, AthletePredictionsResult } from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())

vi.mock('../services/user', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/user')>()),
  fetchAthletePredictions: mockFetch,
}))

function prediction(overrides: Partial<AthletePrediction>): AthletePrediction {
  return {
    id: 'p',
    prediction: 'You will be fresh on Saturday',
    expectedOutcome: 'Legs rated fresh on Saturday',
    actualOutcome: null,
    horizon: 'Saturday',
    category: 'recovery',
    confidence: 0.5,
    status: 'correct',
    createdAt: '2026-10-01T00:00:00Z',
    evaluatedAt: '2026-10-04T00:00:00Z',
    updatedAt: '2026-10-04T00:00:00Z',
    ...overrides,
  }
}

function renderCard(result: AthletePredictionsResult) {
  mockFetch.mockResolvedValue(result)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <PredictionScoreboard />
    </QueryClientProvider>
  )
  return { ...view, queryClient }
}

describe('PredictionScoreboard (ai-trainer-ops#16)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ authToken: 'tok' })
  })

  it('shows the hit rate and lists every miss with what happened instead', async () => {
    renderCard({
      accuracy: { evaluated: 7, correct: 5, accuracy: 5 / 7 },
      predictions: [
        prediction({ id: 'a', status: 'correct' }),
        prediction({
          id: 'b',
          status: 'incorrect',
          prediction: 'Your FTP will rise by 5 W',
          expectedOutcome: 'FTP estimate at least 255 W',
          actualOutcome: 'FTP estimate stayed at 250 W',
        }),
        prediction({ id: 'c', status: 'incorrect', prediction: 'You will ride Sunday' }),
        prediction({ id: 'd', status: 'pending', prediction: 'Still open' }),
      ],
    })

    expect(await screen.findByText('5 came true (71%)')).toBeInTheDocument()
    expect(screen.getByText(/made 7 checkable predictions/)).toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledWith('tok', { includeResolved: true })

    await userEvent.click(screen.getByText('The 2 it got wrong'))
    expect(screen.getByText('Your FTP will rise by 5 W')).toBeVisible()
    expect(screen.getByText('What happened instead: FTP estimate stayed at 250 W')).toBeVisible()
    expect(screen.getByText('What happened instead: not recorded')).toBeVisible()
    expect(screen.queryByText('Still open')).toBeNull()
    // No confidence figure until ai-trainer-ops#39 Q4 says what it means.
    expect(screen.queryByText(/confidence/i)).toBeNull()
  })

  it('says so when nothing was missed', async () => {
    renderCard({
      accuracy: { evaluated: 1, correct: 1, accuracy: 1 },
      predictions: [prediction({ id: 'a' })],
    })

    expect(await screen.findByText(/made 1 checkable prediction about you/)).toBeInTheDocument()
    expect(screen.getByText(/none missed so far/)).toBeInTheDocument()
    expect(screen.queryByText(/got wrong/)).toBeNull()
  })

  it('renders nothing before a prediction has been scored', async () => {
    const { container, queryClient } = renderCard({
      accuracy: { evaluated: 0, correct: 0, accuracy: null },
      predictions: [prediction({ status: 'pending' })],
    })

    // Settled, not merely started: before the data arrives the card is empty
    // for an unrelated reason.
    await waitFor(() =>
      expect(queryClient.getQueryCache().getAll()[0]?.state.status).toBe('success')
    )
    expect(container).toBeEmptyDOMElement()
  })
})
