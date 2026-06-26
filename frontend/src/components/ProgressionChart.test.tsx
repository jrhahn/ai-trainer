import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProgressionChart from './ProgressionChart'
import type { AthleteMetricSnapshot, StravaConnection } from '../store/useAppStore'

interface StoreState {
  metricsHistory: AthleteMetricSnapshot[]
  authToken: string | null
  stravaConnection: StravaConnection | null
}

let storeState: StoreState = { metricsHistory: [], authToken: 'tok', stravaConnection: null }
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (s: StoreState) => unknown) => selector(storeState),
}))

const mockRecalculateAll = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useMetricsPipeline', () => ({
  useMetricsPipeline: () => ({ recalculateAll: mockRecalculateAll }),
}))

const { mockTriggerImport, mockGetProgress } = vi.hoisted(() => ({
  mockTriggerImport: vi.fn(),
  mockGetProgress: vi.fn(),
}))
vi.mock('../services/strava', () => ({
  triggerStravaHistoryImport: mockTriggerImport,
  getStravaImportProgress: mockGetProgress,
}))

function snapshot(overrides: Partial<AthleteMetricSnapshot> = {}): AthleteMetricSnapshot {
  return { recordedAt: '2024-05-01T00:00:00Z', source: 'analysis', ...overrides }
}

function renderChart() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ProgressionChart />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  storeState = { metricsHistory: [], authToken: 'tok', stravaConnection: null }
  mockRecalculateAll.mockResolvedValue(undefined)
})

describe('ProgressionChart', () => {
  it('shows the empty state with fewer than two snapshots', () => {
    storeState.metricsHistory = [snapshot({ ctl: 50 })]
    renderChart()
    expect(
      screen.getByText('Come back after your next analysis to see your fitness progression chart.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Recalculate/ })).toBeInTheDocument()
  })

  it('renders summary badges and charts for a populated history', () => {
    storeState.metricsHistory = [
      snapshot({ recordedAt: '2024-04-01T00:00:00Z', ftp: 250, thresholdHR: 160, ctl: 40, atl: 55, tsb: -15 }),
      snapshot({ recordedAt: '2024-05-01T00:00:00Z', ftp: 265, thresholdHR: 165, ctl: 52.4, atl: 61.1, tsb: 8.2 }),
    ]
    renderChart()

    expect(screen.getByText('2 data points')).toBeInTheDocument()
    expect(screen.getByText('FTP')).toBeInTheDocument()
    expect(screen.getByText('Fitness (CTL)')).toBeInTheDocument()
    expect(screen.getByText('52.4')).toBeInTheDocument()
    expect(screen.getByText('+8.2')).toBeInTheDocument() // positive TSB
    expect(screen.getByLabelText('FTP in Watts')).toBeInTheDocument()
    expect(screen.getByLabelText('Threshold HR in bpm')).toBeInTheDocument()
    expect(screen.getByLabelText('Training stress score')).toBeInTheDocument()
  })

  it('recalculates without a Strava import when not connected', async () => {
    storeState.metricsHistory = [
      snapshot({ ctl: 40, atl: 55, tsb: -15 }),
      snapshot({ recordedAt: '2024-05-01T00:00:00Z', ctl: 52, atl: 61, tsb: -9 }),
    ]
    renderChart()

    await userEvent.click(screen.getByRole('button', { name: /Recalculate/ }))

    await waitFor(() => expect(mockRecalculateAll).toHaveBeenCalledTimes(1))
    expect(mockTriggerImport).not.toHaveBeenCalled()
    expect(await screen.findByText('Done!')).toBeInTheDocument()
  })
})
