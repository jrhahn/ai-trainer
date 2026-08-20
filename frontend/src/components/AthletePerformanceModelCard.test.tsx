import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AthletePerformanceModelCard from './AthletePerformanceModelCard'
import type {
  AthleteHypothesis,
  AthletePerformanceModel,
} from '../services/user'

const mockFetchModel = vi.hoisted(() => vi.fn())
const mockRefreshModel = vi.hoisted(() => vi.fn())
const mockFetchHypotheses = vi.hoisted(() => vi.fn())

vi.mock('../services/ai', () => ({
  fetchAthletePerformanceModel: mockFetchModel,
  refreshAthletePerformanceModel: mockRefreshModel,
}))

vi.mock('../services/user', () => ({
  fetchAthleteHypotheses: mockFetchHypotheses,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function makeModel(overrides: Partial<AthletePerformanceModel> = {}): AthletePerformanceModel {
  return {
    attributes: {
      ftp: {
        estimate: 250,
        score: null,
        confidence: 0.72,
        unit: 'W',
        evidence: ['20-min best power of 263 W across 4 rides'],
        missingInformation: ['a recent maximal 20-min effort'],
      },
      vo2max: {
        estimate: null,
        score: 'unknown',
        confidence: 0.1,
        unit: null,
        evidence: [],
        missingInformation: ['body weight'],
      },
    },
    likelyLimiter: 'threshold',
    limiters: [
      {
        limiter: 'threshold',
        confidence: 0.62,
        evidence: ['FTP ~250 W sits far below MAP ~360 W'],
        counterEvidence: ['few long rides to confirm durability'],
      },
    ],
    recommendations: {
      sufficient: true,
      limiter: 'threshold',
      confidence: 0.62,
      hypothesis: 'Threshold work has a higher expected return than more VO₂max.',
      rationale: 'MAP well above FTP.',
      expectedGain: [],
      weeklyEmphasis: [],
    },
    sourceWindowDays: 90,
    derivedFromRides: 6,
    updatedAt: '2026-07-20T00:00:00Z',
    ...overrides,
  }
}

function makeHypothesis(overrides: Partial<AthleteHypothesis> = {}): AthleteHypothesis {
  return {
    id: 'h1',
    statement: 'The current limiter is likely threshold utilization.',
    category: 'performance_model',
    rationale: '',
    confidence: 0.62,
    evidence: ['FTP ~250 W vs MAP ~360 W (69% of the ceiling).'],
    alternativeExplanations: ['The MAP estimate may be inflated by one short effort.'],
    evidenceCount: 2,
    status: 'proposed',
    firstProposedAt: '2026-07-18T00:00:00Z',
    updatedAt: '2026-07-20T00:00:00Z',
    ...overrides,
  }
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AthletePerformanceModelCard />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchHypotheses.mockResolvedValue([])
})

describe('AthletePerformanceModelCard', () => {
  it('renders attributes with value, confidence, evidence and missing info', async () => {
    mockFetchModel.mockResolvedValue(makeModel())
    renderCard()

    expect(await screen.findByText('Threshold (FTP)')).toBeInTheDocument()
    expect(screen.getByText('250 W')).toBeInTheDocument()
    // Confidence rendered as a labelled bar (not a bare fact).
    expect(
      screen.getByLabelText(/Confidence 72 percent, high confidence/)
    ).toBeInTheDocument()
    // Evidence and missing information both surface.
    expect(screen.getByText(/20-min best power of 263 W/)).toBeInTheDocument()
    expect(screen.getByText(/a recent maximal 20-min effort/)).toBeInTheDocument()
  })

  it('shows the range and the validating test for an unsettled estimate', async () => {
    // #604: hard intervals prove a floor, not a threshold, so the estimate is
    // offered as a range with the test that would settle it.
    const model = makeModel()
    model.attributes.ftp = {
      ...model.attributes.ftp,
      estimate: 316,
      estimateLow: 299,
      estimateHigh: 333,
      validationProtocol: '20-minute threshold test after two easy days.',
    }
    mockFetchModel.mockResolvedValue(model)
    renderCard()

    expect(await screen.findByText('316 W')).toBeInTheDocument()
    expect(screen.getByText(/299–333 W/)).toBeInTheDocument()
    expect(screen.getByText(/20-minute threshold test after two easy days/)).toBeInTheDocument()
  })

  it('omits the range and test for a settled estimate', async () => {
    mockFetchModel.mockResolvedValue(makeModel())
    renderCard()

    expect(await screen.findByText('250 W')).toBeInTheDocument()
    expect(screen.queryByText(/Plausible range/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Test that would settle it/)).not.toBeInTheDocument()
  })

  it('highlights the likely limiter as an inference with evidence and counter-evidence', async () => {
    mockFetchModel.mockResolvedValue(makeModel())
    renderCard()

    expect(
      await screen.findByText(/Likely current limiter: Threshold utilization/)
    ).toBeInTheDocument()
    expect(screen.getByText(/FTP ~250 W sits far below MAP ~360 W/)).toBeInTheDocument()
    expect(screen.getByText(/few long rides to confirm durability/)).toBeInTheDocument()
    // The framing is tentative, never asserted as fact.
    expect(
      screen.getByText(/estimates with a confidence, not measured facts/i)
    ).toBeInTheDocument()
  })

  it('lists performance-model hypotheses with alternative explanations', async () => {
    mockFetchModel.mockResolvedValue(makeModel())
    mockFetchHypotheses.mockResolvedValue([
      makeHypothesis(),
      // Filtered out: not a deterministic performance-model hypothesis.
      makeHypothesis({ id: 'h2', category: 'fatigue_response' }),
      // Filtered out: already resolved.
      makeHypothesis({ id: 'h3', status: 'confirmed' }),
    ])
    renderCard()

    expect(await screen.findByText('Working hypotheses')).toBeInTheDocument()
    expect(
      screen.getByText('The current limiter is likely threshold utilization.')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/The MAP estimate may be inflated by one short effort/)
    ).toBeInTheDocument()
    // Only the single open performance_model hypothesis is shown.
    expect(screen.getAllByText(/Could also be:/)).toHaveLength(1)
  })

  it('shows a low-data state when no attributes are inferred yet', async () => {
    mockFetchModel.mockResolvedValue(
      makeModel({ attributes: {}, limiters: [], recommendations: null })
    )
    renderCard()

    expect(await screen.findByText(/Not enough training data yet/)).toBeInTheDocument()
    expect(screen.queryByText('Inferred attributes')).not.toBeInTheDocument()
  })

  it('shows an error state when the model fails to load', async () => {
    mockFetchModel.mockRejectedValue(new Error('boom'))
    renderCard()

    expect(
      await screen.findByText(/Could not load the performance model/)
    ).toBeInTheDocument()
  })

  it('refreshes the model from training history', async () => {
    mockFetchModel.mockResolvedValue(makeModel())
    mockRefreshModel.mockResolvedValue(makeModel({ derivedFromRides: 9 }))
    renderCard()

    await screen.findByText('Threshold (FTP)')
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }))

    await waitFor(() => expect(mockRefreshModel).toHaveBeenCalledWith('test-token'))
  })
})
