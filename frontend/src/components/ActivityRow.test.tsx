import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ActivityRow from './ActivityRow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'

const ride: RideMetricPoint = {
  stravaActivityId: 7,
  activityDate: '2026-10-27',
  sportType: 'Ride',
  activityName: 'Gravel loop',
  durationSeconds: 5400,
  tss: 45,
  tssSource: 'duration',
}

afterEach(() => useAppStore.setState({ isExpertMode: false }))

describe('ActivityRow: the ride’s load (ai-trainer-ops#311)', () => {
  it('shows it after the duration in expert mode', () => {
    useAppStore.setState({ isExpertMode: true })
    render(<ActivityRow ride={ride} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} />)

    expect(screen.getByText('Load ≈ 45')).toHaveAttribute('title', 'Load estimated from the duration only')
  })

  it('leaves it out otherwise', () => {
    render(<ActivityRow ride={ride} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} />)

    expect(screen.getByText('Gravel loop')).toBeInTheDocument()
    expect(screen.queryByText(/Load/)).toBeNull()
  })
})
