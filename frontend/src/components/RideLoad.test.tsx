import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RideLoadInRow, RideLoadLine } from './RideLoad'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'

const ride = (fields: Partial<RideMetricPoint>): RideMetricPoint => ({
  stravaActivityId: 1,
  activityDate: '2026-10-27',
  sportType: 'Ride',
  ...fields,
})

afterEach(() => useAppStore.setState({ isExpertMode: false, uiLanguage: 'en' }))

describe('a ride’s load in expert mode (ai-trainer-ops#311)', () => {
  it('shows a measured load plainly in the row', () => {
    useAppStore.setState({ isExpertMode: true, uiLanguage: 'en' })
    render(<RideLoadInRow ride={ride({ tss: 85, tssSource: 'power' })} />)

    const load = screen.getByText('Load 85')
    expect(load).not.toHaveAttribute('title')
  })

  it('marks an estimate in the row and says on hover what it came from', () => {
    useAppStore.setState({ isExpertMode: true, uiLanguage: 'en' })
    render(<RideLoadInRow ride={ride({ tss: 45, tssSource: 'heart_rate' })} />)

    expect(screen.getByText('Load ≈ 45')).toHaveAttribute('title', 'Load estimated from your heart rate')
  })

  it('says on the day what each estimate came from', () => {
    useAppStore.setState({ isExpertMode: true, uiLanguage: 'en' })
    render(
      <>
        <RideLoadLine ride={ride({ tss: 30, tssSource: 'duration' })} />
        <RideLoadLine ride={ride({ stravaActivityId: 2, tss: 52, tssSource: 'rpe' })} />
        <RideLoadLine ride={ride({ stravaActivityId: 3, tss: 85, tssSource: 'provider' })} />
      </>,
    )

    expect(screen.getByText('Load ≈ 30 · estimated from the duration only')).toBeInTheDocument()
    expect(screen.getByText('Load ≈ 52 · estimated from the effort you logged')).toBeInTheDocument()
    expect(screen.getByText('Load 85')).toBeInTheDocument()
  })

  it('reads German on a German screen', () => {
    useAppStore.setState({ isExpertMode: true, uiLanguage: 'de' })
    render(
      <>
        <RideLoadInRow ride={ride({ tss: 45, tssSource: 'heart_rate' })} />
        <RideLoadLine ride={ride({ stravaActivityId: 2, tss: 45, tssSource: 'heart_rate' })} />
      </>,
    )

    expect(screen.getByText('Belastung ≈ 45')).toHaveAttribute('title', 'Belastung aus deiner Herzfrequenz geschätzt')
    expect(screen.getByText('Belastung ≈ 45 · aus deiner Herzfrequenz geschätzt')).toBeInTheDocument()
  })

  it('shows nothing outside expert mode', () => {
    useAppStore.setState({ isExpertMode: false, uiLanguage: 'en' })
    const { container } = render(
      <>
        <RideLoadInRow ride={ride({ tss: 45, tssSource: 'heart_rate' })} />
        <RideLoadLine ride={ride({ tss: 85, tssSource: 'power' })} />
      </>,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows nothing for a ride without a load', () => {
    useAppStore.setState({ isExpertMode: true, uiLanguage: 'en' })
    const { container } = render(<RideLoadLine ride={ride({})} />)

    expect(container).toBeEmptyDOMElement()
  })
})
