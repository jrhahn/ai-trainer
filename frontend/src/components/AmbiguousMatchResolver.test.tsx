import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AmbiguousMatchResolver from './AmbiguousMatchResolver'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'

const mockResolve = vi.hoisted(() => vi.fn())
const mockFetchRides = vi.hoisted(() => vi.fn())
const mockFetchPlan = vi.hoisted(() => vi.fn())

vi.mock('../services/ai', () => ({ resolveRideMatch: mockResolve }))
vi.mock('../services/user', () => ({
  fetchRideMetricsHistory: mockFetchRides,
  fetchTrainingPlan: mockFetchPlan,
}))

const DATE = '2026-05-06'

function makeRide(overrides: Partial<RideMetricPoint> = {}): RideMetricPoint {
  return {
    stravaActivityId: 7001,
    activityDate: DATE,
    activityName: 'Morning spin',
    sportType: 'Ride',
    planMatchStatus: 'ambiguous',
    ...overrides,
  }
}

function makeSession(overrides: Partial<TrainingDay> = {}): TrainingDay {
  return {
    date: DATE,
    workoutType: 'endurance',
    title: 'Endurance',
    description: 'Steady',
    durationMinutes: 90,
    ...overrides,
  }
}

const TWO_A_DAY: TrainingDay[] = [
  makeSession({ slot: 0, timeOfDay: 'AM', title: 'Easy spin' }),
  makeSession({ slot: 1, timeOfDay: 'PM', title: 'VO2max intervals', workoutType: 'intervals' }),
]

function renderResolver(ride: RideMetricPoint) {
  // No QueryClientProvider on purpose: the resolver is embedded in activity rows on
  // pages that do not otherwise need one, so it must not require ambient context.
  return render(<AmbiguousMatchResolver ride={ride} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'test-token', trainingPlan: TWO_A_DAY })
  mockResolve.mockResolvedValue({
    ride: makeRide({ planMatchStatus: 'manual_matched' }),
    coachNote: null,
  })
  mockFetchRides.mockResolvedValue([])
  mockFetchPlan.mockResolvedValue(TWO_A_DAY)
})

describe('AmbiguousMatchResolver', () => {
  // -------------------------------------------------------------------------
  // When it stays out of the way
  // -------------------------------------------------------------------------

  it('renders nothing for a ride the matcher already attributed', () => {
    const { container } = renderResolver(makeRide({ planMatchStatus: 'auto_matched' }))

    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when nothing was planned that day', () => {
    // Without a planned session there is no question to ask: the ride is simply
    // unplanned, which the row already says.
    useAppStore.setState({ trainingPlan: [] })
    const { container } = renderResolver(makeRide())

    expect(container).toBeEmptyDOMElement()
  })

  // -------------------------------------------------------------------------
  // Asking the question
  // -------------------------------------------------------------------------

  it('offers every session of a two-a-day', () => {
    renderResolver(makeRide())

    expect(screen.getByText('Which session was this?')).toBeInTheDocument()
    expect(screen.getByText('Easy spin')).toBeInTheDocument()
    expect(screen.getByText('VO2max intervals')).toBeInTheDocument()
  })

  it('asks a yes/no question when the day holds one session', () => {
    useAppStore.setState({ trainingPlan: [makeSession({ title: 'Threshold' })] })
    renderResolver(makeRide())

    // One planned session and several rides: the open question is whether *this*
    // ride was it, not which of them.
    expect(screen.getByText('Was this your planned session?')).toBeInTheDocument()
    expect(screen.getByText('Yes — Threshold')).toBeInTheDocument()
  })

  it('names the ride in each accessible label', () => {
    // Two ambiguous rides on one day is the normal case here, so a label of just
    // "PM VO2max intervals" would offer a screen reader the same choice twice.
    renderResolver(makeRide({ activityName: 'Evening hammerfest' }))

    expect(
      screen.getByLabelText('Match “Evening hammerfest” to PM VO2max intervals')
    ).toBeInTheDocument()
  })

  // -------------------------------------------------------------------------
  // Answering it
  // -------------------------------------------------------------------------

  it('sends the slot of the session the athlete picked', async () => {
    renderResolver(makeRide())

    await userEvent.click(
      screen.getByLabelText('Match “Morning spin” to PM VO2max intervals')
    )

    // Slot 1, not the day's first session — the whole point of #547's other half.
    await waitFor(() => {
      expect(mockResolve).toHaveBeenCalledWith('test-token', DATE, 7001, 1, undefined)
    })
  })

  it('sends slot 0 for a single-session day', async () => {
    useAppStore.setState({ trainingPlan: [makeSession({ title: 'Threshold' })] })
    renderResolver(makeRide())

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(mockResolve).toHaveBeenCalledWith('test-token', DATE, 7001, 0, undefined)
    })
  })

  it('re-reads the plan and the ride history, not just the resolved ride', async () => {
    // The resolve also unmatches whatever else claimed that session and marks it
    // completed. Neither is in the response, so trusting it alone would leave the
    // other ride amber and the calendar unticked.
    renderResolver(makeRide())

    await userEvent.click(screen.getByLabelText('Match “Morning spin” to AM Easy spin'))

    await waitFor(() => {
      expect(mockFetchRides).toHaveBeenCalledWith('test-token')
    })
    expect(mockFetchPlan).toHaveBeenCalledWith('test-token')
  })

  it('puts the resolved ride into the store', async () => {
    useAppStore.setState({ rideMetricsHistory: [makeRide()] })
    renderResolver(makeRide())

    await userEvent.click(screen.getByLabelText('Match “Morning spin” to AM Easy spin'))

    await waitFor(() => {
      expect(useAppStore.getState().trainingPlan).toEqual(TWO_A_DAY)
    })
  })

  it("shows the coach's note once the review comes back", async () => {
    mockResolve.mockResolvedValue({
      ride: makeRide({ planMatchStatus: 'manual_matched' }),
      coachNote: 'Good call — that was the interval session.',
    })
    renderResolver(makeRide())

    await userEvent.click(screen.getByLabelText('Match “Morning spin” to AM Easy spin'))

    await waitFor(() => {
      expect(
        screen.getByText('Good call — that was the interval session.')
      ).toBeInTheDocument()
    })
    // The question is answered, so it stops being asked.
    expect(screen.queryByText('Which session was this?')).not.toBeInTheDocument()
  })

  it('reports a failed resolve instead of pretending it worked', async () => {
    mockResolve.mockRejectedValue(new Error('nope'))
    renderResolver(makeRide())

    await userEvent.click(screen.getByLabelText('Match “Morning spin” to AM Easy spin'))

    await waitFor(() => {
      expect(screen.getByText(/Could not save that/)).toBeInTheDocument()
    })
    // And keeps the choice on screen so it can be retried.
    expect(screen.getByText('Which session was this?')).toBeInTheDocument()
  })

  it("sends the ride's provider id so a non-Strava ride can be found (#441)", async () => {
    // The production failure: a synthesized 63-bit id is rounded on its way
    // through JS, the backend finds no row, and the card says "Could not save
    // that". The string id is what actually identifies the ride.
    useAppStore.setState({ trainingPlan: [makeSession({ title: 'Recovery' })] })
    renderResolver(
      makeRide({ stravaActivityId: 7846148097020609552, externalActivityId: 'i174087702' })
    )

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(mockResolve).toHaveBeenCalledWith(
        'test-token',
        DATE,
        7846148097020609552,
        0,
        'i174087702'
      )
    })
  })
})
