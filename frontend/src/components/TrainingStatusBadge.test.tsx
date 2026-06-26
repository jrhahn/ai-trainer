import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TrainingStatusBadge from './TrainingStatusBadge'
import type { AthleteMetricSnapshot } from '../store/useAppStore'

function snapshot(overrides: Partial<AthleteMetricSnapshot> = {}): AthleteMetricSnapshot {
  return { recordedAt: '2024-05-01', source: 'test', ...overrides }
}

/** Build a history of `count` snapshots whose last entry carries `latest`. */
function history(latest: Partial<AthleteMetricSnapshot>, count = 7): AthleteMetricSnapshot[] {
  const filler = Array.from({ length: count - 1 }, () => snapshot())
  return [...filler, snapshot(latest)]
}

describe('TrainingStatusBadge', () => {
  it('renders nothing with fewer than 7 snapshots', () => {
    const { container } = render(
      <TrainingStatusBadge metricsHistory={history({ tsb: 20 }, 6)} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('labels TSB > 10 as Fresh', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: 15 })} />)
    expect(screen.getByText('Fresh · TSB +15')).toBeInTheDocument()
  })

  it('labels TSB between -5 and 10 as Building', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: 3 })} />)
    expect(screen.getByText('Building · TSB 3')).toBeInTheDocument()
  })

  it('labels TSB between -20 and -5 as Loaded', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: -12 })} />)
    expect(screen.getByText('Loaded · TSB -12')).toBeInTheDocument()
  })

  it('labels TSB <= -20 as Overreaching', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: -25 })} />)
    expect(screen.getByText('Overreaching · TSB -25')).toBeInTheDocument()
  })

  it('defaults missing TSB to 0 (Building)', () => {
    render(<TrainingStatusBadge metricsHistory={history({})} />)
    expect(screen.getByText('Building · TSB 0')).toBeInTheDocument()
  })

  it('shows fitness and fatigue when ctl and atl are present', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: 5, ctl: 80, atl: 75 })} />)
    expect(screen.getByText(/Fitness 80/)).toBeInTheDocument()
    expect(screen.getByText(/Fatigue 75/)).toBeInTheDocument()
  })

  it('omits fatigue when atl is absent', () => {
    render(<TrainingStatusBadge metricsHistory={history({ tsb: 5, ctl: 80 })} />)
    expect(screen.getByText(/Fitness 80/)).toBeInTheDocument()
    expect(screen.queryByText(/Fatigue/)).not.toBeInTheDocument()
  })
})
