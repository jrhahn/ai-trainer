import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
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

describe('ActivityRow: the leg-feel buttons (ai-trainer-ops#338 F4)', () => {
  afterEach(() => useAppStore.setState({ authToken: null, uiLanguage: 'en' }))

  it('says what they are on the screen, not only in a tooltip', () => {
    useAppStore.setState({ authToken: 'tok' })
    const { rerender } = render(<ActivityRow ride={ride} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} />)

    const group = screen.getByRole('group', { name: 'How did your legs feel?' })
    expect(within(group).getByText('Legs')).toBeVisible()
    expect(within(group).getAllByRole('button')).toHaveLength(3)

    useAppStore.setState({ uiLanguage: 'de' })
    rerender(<ActivityRow ride={ride} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} />)
    expect(within(screen.getByRole('group')).getByText('Beine')).toBeVisible()
  })
})

describe('ActivityRow: stacked on a phone (ai-trainer-ops#338 F4)', () => {
  // jsdom lays nothing out, so the classes are the contract: a bare `truncate`
  // cuts at every width, `sm:truncate` only from the sm breakpoint up.
  const longRide = { ...ride, activityName: 'Long gravel loop over the hills and back by the lake' }

  it('wraps the whole name and the whole planned session instead of cutting them', () => {
    render(<ActivityRow ride={longRide} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} stacked />)

    const name = screen.getByTestId('activity-name')
    const planned = screen.getByTestId('activity-planned')
    expect(name).toHaveTextContent('Long gravel loop over the hills and back by the lake')
    for (const element of [name, planned]) {
      expect(element).not.toHaveClass('truncate')
      expect(element).toHaveClass('break-words', 'sm:truncate')
    }
    // The label goes with the planned title, so it never stands on a line alone.
    expect(planned).toHaveTextContent(/^planned: /)
  })

  it('keeps the one-line row everywhere else', () => {
    render(<ActivityRow ride={longRide} isNew={false} onEdit={vi.fn()} onRemove={vi.fn()} />)

    expect(screen.getByTestId('activity-name')).toHaveClass('truncate')
    expect(screen.getByTestId('activity-planned')).toHaveClass('truncate')
  })
})
