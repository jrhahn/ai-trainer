import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import DayActivities from './DayActivities'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'

const day = '2026-10-07'
const ride = (fields: Partial<RideMetricPoint>): RideMetricPoint => ({
  stravaActivityId: 1, activityDate: day, sportType: 'Ride', activityName: 'Morning Ride', durationSeconds: 5400, ...fields,
})

beforeEach(() => useAppStore.getState().resetAll())

describe('DayActivities (ai-trainer-ops#47, #48)', () => {
  it('lists what happened on the day, recorded or entered', () => {
    useAppStore.setState({
      rideMetricsHistory: [
        ride({}),
        ride({ stravaActivityId: 2, sportType: 'running', activitySource: 'logged', externalActivityId: `${day}#100`, durationSeconds: 1800 }),
        ride({ stravaActivityId: 3, activityDate: '2026-10-06' }),
      ],
    })
    render(<DayActivities date={day} />)

    expect(screen.getByText('Morning Ride')).toBeInTheDocument()
    expect(screen.getByText('1h 30m')).toBeInTheDocument()
    expect(screen.getByText('Entered by you')).toBeInTheDocument()
    expect(screen.getByText('30 min')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('shows one row per activity even when the list holds it twice', () => {
    useAppStore.setState({ rideMetricsHistory: [ride({ externalActivityId: 'i1' }), ride({ externalActivityId: 'i1' })] })
    render(<DayActivities date={day} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('leaves out a duration it does not know, and names an unnamed activity', () => {
    useAppStore.setState({ rideMetricsHistory: [ride({ activityName: null, durationSeconds: undefined })] })
    render(<DayActivities date={day} />)
    expect(screen.getByText('Activity')).toBeInTheDocument()
    expect(screen.queryByText(/min|h /)).not.toBeInTheDocument()
  })

  it('renders nothing for a day with nothing on it', () => {
    const { container } = render(<DayActivities date={day} />)
    expect(container).toBeEmptyDOMElement()
  })
})
