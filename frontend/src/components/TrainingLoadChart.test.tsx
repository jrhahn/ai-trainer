import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import TrainingLoadChart from './TrainingLoadChart'
import type { RideMetricPoint } from '../store/useAppStore'

let rideMetricsHistory: RideMetricPoint[] = []
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (s: { rideMetricsHistory: RideMetricPoint[] }) => unknown) =>
    selector({ rideMetricsHistory }),
}))

function ride(overrides: Partial<RideMetricPoint> = {}): RideMetricPoint {
  return {
    stravaActivityId: 1,
    activityDate: '2024-05-01',
    sportType: 'cycling',
    tss: 80,
    ctlAfter: 50,
    atlAfter: 60,
    tsbAfter: -10,
    ...overrides,
  }
}

function renderChart(history: RideMetricPoint[]) {
  rideMetricsHistory = history
  return render(<TrainingLoadChart />)
}

describe('TrainingLoadChart', () => {
  it('shows the empty hint with fewer than two activities', () => {
    renderChart([ride()])
    expect(
      screen.getByText('Sync your Strava activities to see how your training load develops over time.')
    ).toBeInTheDocument()
  })

  it('renders summary badges and charts for a populated history', () => {
    renderChart([
      ride({ activityDate: '2024-05-01', ctlAfter: 40, atlAfter: 55, tsbAfter: -15, tss: 70 }),
      ride({ stravaActivityId: 2, activityDate: '2024-05-03', ctlAfter: 52.4, atlAfter: 61.2, tsbAfter: -8.8, tss: 95 }),
    ])

    expect(screen.getByText('2 activities')).toBeInTheDocument()
    expect(screen.getByText('Fitness (CTL)')).toBeInTheDocument()
    expect(screen.getByText('52.4')).toBeInTheDocument()
    expect(screen.getByText('Fatigue (ATL)')).toBeInTheDocument()
    expect(screen.getByText('Form (TSB)')).toBeInTheDocument()
    expect(screen.getByText('-8.8')).toBeInTheDocument()
    expect(screen.getByText('Last TSS')).toBeInTheDocument()
    // CTL / ATL / TSB / TSS line charts
    expect(screen.getByLabelText('CTL')).toBeInTheDocument()
    expect(screen.getByLabelText('ATL')).toBeInTheDocument()
    expect(screen.getByLabelText('TSB')).toBeInTheDocument()
    expect(screen.getByLabelText('TSS per activity')).toBeInTheDocument()
  })

  it('renders a positive TSB with a + sign', () => {
    renderChart([
      ride({ activityDate: '2024-05-01', tsbAfter: 5 }),
      ride({ stravaActivityId: 2, activityDate: '2024-05-03', tsbAfter: 12.5 }),
    ])
    expect(screen.getByText('+12.5')).toBeInTheDocument()
  })
})

describe('estimated load (#579)', () => {
  it('calls the series TSS while every load was measured', () => {
    renderChart([
      ride({ activityDate: '2024-05-01', tss: 70, tssSource: 'power' }),
      ride({ stravaActivityId: 2, activityDate: '2024-05-03', tss: 95, tssSource: 'provider' }),
    ])
    expect(screen.getByText(/Daily TSS/)).toBeInTheDocument()
    expect(screen.queryByText(/estimated load/i)).not.toBeInTheDocument()
  })

  it('stops calling it TSS once an estimate is in the series', () => {
    renderChart([
      ride({ activityDate: '2024-05-01', tss: 70, tssSource: 'power' }),
      ride({
        stravaActivityId: 2,
        activityDate: '2024-05-03',
        sportType: 'WeightTraining',
        tss: 34,
        tssSource: 'heart_rate',
      }),
    ])
    expect(screen.queryByText(/Daily TSS/)).not.toBeInTheDocument()
    expect(screen.getByText(/some estimated/i)).toBeInTheDocument()
    expect(screen.getByText(/not a measured TSS/i)).toBeInTheDocument()
  })

  it('leaves a legacy row without a recorded source alone', () => {
    renderChart([
      ride({ activityDate: '2024-05-01', tss: 70 }),
      ride({ stravaActivityId: 2, activityDate: '2024-05-03', tss: 95 }),
    ])
    expect(screen.getByText(/Daily TSS/)).toBeInTheDocument()
  })
})
