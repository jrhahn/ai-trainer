import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { addDays } from 'date-fns'
import SeasonCountdown from './SeasonCountdown'
import { useAppStore } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

function setProfile(raceDate?: string, raceDescription?: string) {
  useAppStore.setState({
    userProfile: {
      name: 'Jonas',
      raceDate,
      raceDescription,
    } as never,
  })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('SeasonCountdown', () => {
  it('counts the days to the target event', () => {
    setProfile(formatLocalDate(addDays(new Date(), 38)), 'Ötztaler Radmarathon — 238 km, 5500 hm')
    render(<SeasonCountdown />)

    expect(screen.getByText('38 days')).toBeInTheDocument()
    // The distance blurb after the dash belongs on the event page, not here.
    expect(screen.getByText('Ötztaler Radmarathon')).toBeInTheDocument()
  })

  it('keeps a hyphenated event name whole', () => {
    setProfile(formatLocalDate(addDays(new Date(), 12)), 'Time-trial championship')
    render(<SeasonCountdown />)

    expect(screen.getByText('Time-trial championship')).toBeInTheDocument()
  })

  it('says race day rather than "0 days"', () => {
    setProfile(formatLocalDate(new Date()), 'Club road race')
    render(<SeasonCountdown />)

    expect(screen.getByText('Race day')).toBeInTheDocument()
  })

  it('renders nothing without a target date', () => {
    setProfile(undefined)
    const { container } = render(<SeasonCountdown />)

    expect(container).toBeEmptyDOMElement()
  })

  it('disappears once the event has passed', () => {
    setProfile(formatLocalDate(addDays(new Date(), -1)), 'Last month race')
    const { container } = render(<SeasonCountdown />)

    expect(container).toBeEmptyDOMElement()
  })
})
