import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ColdStartNote, { COLD_START_ACTIVITIES } from './ColdStartNote'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, UserProfile } from '../store/useAppStore'

function setState(profile: Partial<UserProfile>, activities: number) {
  useAppStore.setState({
    userProfile: {
      name: 'Mara',
      trainingGoal: 'general_fitness',
      fitnessLevel: 'beginner',
      ...profile,
    } as UserProfile,
    rideMetricsHistory: Array.from({ length: activities }, () => ({}) as RideMetricPoint),
  })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('ColdStartNote', () => {
  it('names the answers the plan used and what it does not know', () => {
    setState({ weeklyHours: 5, trainingDays: ['tuesday', 'saturday'] }, 0)
    render(<ColdStartNote />)

    expect(screen.getByText('It used: general fitness, 5 h a week, Tue, Sat, beginner level.')).toBeInTheDocument()
    expect(
      screen.getByText('It does not know yet: your FTP, your recent training load, how you respond to sessions.')
    ).toBeInTheDocument()
    expect(screen.getByText(/keep the first week moderate/)).toBeInTheDocument()
  })

  it('counts a stated FTP as known', () => {
    setState({ currentFTP: 240 }, 0)
    render(<ColdStartNote />)

    expect(screen.getByText(/FTP 240 W/)).toBeInTheDocument()
    expect(screen.getByText(/It does not know yet: your recent training load/)).toBeInTheDocument()
  })

  it('counts the first activities instead of promising a moderate week', () => {
    // The backend's moderate-week rule holds only while nothing is on record.
    setState({}, 3)
    render(<ColdStartNote />)

    expect(screen.getByText(`3 of ${COLD_START_ACTIVITIES} activities so far. Every one replaces a guess with a measurement.`)).toBeInTheDocument()
    expect(screen.queryByText(/moderate/)).not.toBeInTheDocument()
  })

  it('goes away once enough activities are synced', () => {
    setState({}, COLD_START_ACTIVITIES)
    const { container } = render(<ColdStartNote />)

    expect(container).toBeEmptyDOMElement()
  })
})
