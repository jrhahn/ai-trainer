import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RaceReadinessCard from './RaceReadinessCard'
import type { ReadinessScore } from '../services/ai'

const mockFetchReadinessScore = vi.hoisted(() => vi.fn())
vi.mock('../services/ai', () => ({ fetchReadinessScore: mockFetchReadinessScore }))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function makeScore(overrides: Partial<ReadinessScore> = {}): ReadinessScore {
  return {
    score: 70,
    formScore: 68,
    fitnessScore: 72,
    ctl: 80.4,
    atl: 70.2,
    tsb: 10.2,
    daysUntilRace: 5,
    raceDate: '2024-06-01',
    projectedScore: null,
    projectedCtl: null,
    projectedAtl: null,
    projectedTsb: null,
    recommendations: [],
    ...overrides,
  }
}

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <RaceReadinessCard />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('RaceReadinessCard', () => {
  it('shows a loading indicator while the score is fetched', () => {
    mockFetchReadinessScore.mockReturnValue(new Promise(() => {}))
    renderCard()
    expect(screen.getByText('Calculating readiness…')).toBeInTheDocument()
  })

  it('shows an error message when the fetch fails', async () => {
    mockFetchReadinessScore.mockRejectedValue(new Error('boom'))
    renderCard()
    expect(await screen.findByText('Could not load readiness score.')).toBeInTheDocument()
  })

  it('renders the status label, metrics and days-to-race badge', async () => {
    mockFetchReadinessScore.mockResolvedValue(
      makeScore({ score: 70, ctl: 80.4, atl: 70.2, tsb: 10.2, daysUntilRace: 5 })
    )
    renderCard()

    expect(await screen.findByText('Race Ready')).toBeInTheDocument()
    expect(screen.getByText('80.4')).toBeInTheDocument() // CTL
    expect(screen.getByText('70.2')).toBeInTheDocument() // ATL
    expect(screen.getByText('+10.2')).toBeInTheDocument() // TSB formatted
    expect(screen.getByText('5d to race')).toBeInTheDocument()
  })

  it('labels a score >= 80 as Peak Form', async () => {
    mockFetchReadinessScore.mockResolvedValue(makeScore({ score: 85 }))
    renderCard()
    expect(await screen.findByText('Peak Form')).toBeInTheDocument()
  })

  it('shows the race-day badge when daysUntilRace is 0', async () => {
    mockFetchReadinessScore.mockResolvedValue(
      makeScore({ daysUntilRace: 0, raceDate: '2024-06-01' })
    )
    renderCard()
    expect(await screen.findByText('🏁 Race day!')).toBeInTheDocument()
  })

  it('renders the projection block when a projected score is provided', async () => {
    mockFetchReadinessScore.mockResolvedValue(
      makeScore({ daysUntilRace: 7, projectedScore: 82, projectedCtl: 85.1, projectedTsb: 12.3 })
    )
    renderCard()
    expect(await screen.findByText('Projected at race day')).toBeInTheDocument()
    expect(screen.getByText(/CTL:/)).toBeInTheDocument()
  })

  it('lists recommendations with source-tagged supporting evidence', async () => {
    mockFetchReadinessScore.mockResolvedValue(
      makeScore({
        recommendations: [
          {
            recommendation: 'Taper this week',
            reasoning: [
              { source: 'personal_observation', text: 'You start races too fast' },
              { source: 'coach_inference', text: 'TSB is 12.0' },
              { source: 'scientific_evidence', text: 'taper lifts race-day form' },
            ],
          },
          { recommendation: 'Sleep 8h', reasoning: [] },
        ],
      })
    )
    renderCard()
    expect(await screen.findByText('Taper this week')).toBeInTheDocument()
    expect(screen.getByText('Sleep 8h')).toBeInTheDocument()
    // Supporting-evidence reasoning is rendered alongside the recommendation.
    expect(screen.getByText('TSB is 12.0')).toBeInTheDocument()
    expect(screen.getByText('taper lifts race-day form')).toBeInTheDocument()
    // Each bullet is labelled with its knowledge source (issue #377).
    expect(screen.getByText('Personal observation')).toBeInTheDocument()
    expect(screen.getByText('Coach inference')).toBeInTheDocument()
    expect(screen.getByText('Scientific evidence')).toBeInTheDocument()
  })

  it('labels a low score as Rest Needed', async () => {
    mockFetchReadinessScore.mockResolvedValue(makeScore({ score: 20, tsb: -25 }))
    renderCard()
    await waitFor(() => expect(screen.getByText('Rest Needed')).toBeInTheDocument())
  })
})
