import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TrainingCalendar from './TrainingCalendar'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'

const {
  mockCreateRaceEvent,
  mockDeleteRaceEventRemote,
  mockUpdateRaceEventRemote,
} = vi.hoisted(() => ({
  mockCreateRaceEvent: vi.fn(),
  mockDeleteRaceEventRemote: vi.fn(),
  mockUpdateRaceEventRemote: vi.fn(),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../services/user', () => ({
  createRaceEvent: mockCreateRaceEvent,
  deleteRaceEventRemote: mockDeleteRaceEventRemote,
  updateRaceEventRemote: mockUpdateRaceEventRemote,
}))

function makeDay(date: string, workoutType: TrainingDay['workoutType'] = 'endurance'): TrainingDay {
  return {
    date,
    workoutType,
    title: `${workoutType} workout`,
    description: 'Training session',
    durationMinutes: 60,
  }
}

function formatIsoDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function monthDate(monthOffset: number, day: number): string {
  const now = new Date()
  return formatIsoDate(new Date(now.getFullYear(), now.getMonth() + monthOffset, day, 12))
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({
    authToken: 'tok-123',
    trainingPlan: [],
    raceEvents: [],
  })
  mockNavigate.mockClear()
  mockCreateRaceEvent.mockReset()
  mockDeleteRaceEventRemote.mockReset()
  mockUpdateRaceEventRemote.mockReset()
})

describe('TrainingCalendar', () => {
  it('renders the day-of-week headers', () => {
    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )
    ;['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach((d) => {
      expect(screen.getByText(d)).toBeInTheDocument()
    })
  })

  it('renders workout titles from the training plan', () => {
    const workoutDate = monthDate(0, 13)
    useAppStore.setState({ trainingPlan: [makeDay(workoutDate, 'intervals')] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    expect(screen.getByText('intervals workout')).toBeInTheDocument()
  })

  it('navigates to the workout page when a day cell is clicked', async () => {
    const workoutDate = monthDate(0, 13)
    useAppStore.setState({ trainingPlan: [makeDay(workoutDate)] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    await userEvent.click(screen.getByText('endurance workout'))
    expect(mockNavigate).toHaveBeenCalledWith(`/workout/${workoutDate}`)
  })

  it('renders empty cells for days not in the plan', () => {
    useAppStore.setState({ trainingPlan: [makeDay(monthDate(0, 13))] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    // The calendar shows a 6-week month grid; not all cells have workout content.
    const workoutCells = screen.queryAllByText(/workout/)
    expect(workoutCells.length).toBeGreaterThan(0)
  })

  it('navigates to a future month and saves a race event there', async () => {
    const futureDate = monthDate(1, 10)
    mockCreateRaceEvent.mockResolvedValue({
      id: 'race-1',
      date: futureDate,
      startTime: null,
      distanceKm: 120,
      elevationM: 1800,
    })

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /next month/i }))
    await user.click(screen.getByRole('button', { name: `Calendar day ${futureDate}` }))

    expect(screen.getByLabelText(/Date/)).toHaveValue(futureDate)

    await user.type(screen.getByLabelText(/Distance/), '120')
    await user.type(screen.getByLabelText(/Climb/), '1800')
    await user.click(screen.getByRole('button', { name: /Add event/i }))

    await waitFor(() => {
      expect(mockCreateRaceEvent).toHaveBeenCalledWith('tok-123', {
        date: futureDate,
        startTime: null,
        distanceKm: 120,
        elevationM: 1800,
      })
    })
    expect(useAppStore.getState().raceEvents).toEqual([
      {
        id: 'race-1',
        date: futureDate,
        startTime: null,
        distanceKm: 120,
        elevationM: 1800,
      },
    ])
  })

  it('deletes an existing race event from a day cell', async () => {
    const date = monthDate(0, 15)
    const event = { id: 'race-1', date, startTime: null, distanceKm: 120, elevationM: 1800 }
    useAppStore.setState({ raceEvents: [event as never] })
    mockDeleteRaceEventRemote.mockResolvedValue(undefined)
    const onRemoved = vi.fn()

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents onRaceEventRemoved={onRemoved} />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: `Calendar day ${date}` }))
    await user.click(screen.getByRole('button', { name: /Remove race event/i }))

    await waitFor(() =>
      expect(mockDeleteRaceEventRemote).toHaveBeenCalledWith('tok-123', 'race-1')
    )
    expect(useAppStore.getState().raceEvents).toEqual([])
    expect(onRemoved).toHaveBeenCalledWith(event)
  })

  it('edits an existing race event', async () => {
    const date = monthDate(0, 15)
    const event = { id: 'race-1', date, startTime: null, distanceKm: 120, elevationM: 1800 }
    useAppStore.setState({ raceEvents: [event as never] })
    mockUpdateRaceEventRemote.mockResolvedValue({ ...event, distanceKm: 150 })

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: `Calendar day ${date}` }))
    await user.click(screen.getByRole('button', { name: /Edit race event/i }))

    const distance = screen.getByLabelText(/Distance/)
    await user.clear(distance)
    await user.type(distance, '150')
    await user.click(screen.getByRole('button', { name: /Save event/i }))

    await waitFor(() =>
      expect(mockUpdateRaceEventRemote).toHaveBeenCalledWith('tok-123', 'race-1', {
        date,
        startTime: null,
        distanceKm: 150,
        elevationM: 1800,
      })
    )
    expect(useAppStore.getState().raceEvents[0].distanceKm).toBe(150)
  })

  it('shows an error when saving a race event fails', async () => {
    const futureDate = monthDate(1, 10)
    mockCreateRaceEvent.mockRejectedValue(new Error('Save exploded'))

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /next month/i }))
    await user.click(screen.getByRole('button', { name: `Calendar day ${futureDate}` }))
    await user.type(screen.getByLabelText(/Distance/), '120')
    await user.type(screen.getByLabelText(/Climb/), '1800')
    await user.click(screen.getByRole('button', { name: /Add event/i }))

    expect(await screen.findByText('Save exploded')).toBeInTheDocument()
  })

  it('shows an error when deleting a race event fails', async () => {
    const date = monthDate(0, 15)
    const event = { id: 'race-1', date, startTime: null, distanceKm: 120, elevationM: 1800 }
    useAppStore.setState({ raceEvents: [event as never] })
    mockDeleteRaceEventRemote.mockRejectedValue(new Error('Delete exploded'))

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: `Calendar day ${date}` }))
    await user.click(screen.getByRole('button', { name: /Remove race event/i }))

    expect(await screen.findByText('Delete exploded')).toBeInTheDocument()
    expect(useAppStore.getState().raceEvents).toHaveLength(1)
  })

  it('closes the event editor and resets the edit form via "New"', async () => {
    const date = monthDate(0, 15)
    const event = { id: 'race-1', date, startTime: null, distanceKm: 120, elevationM: 1800 }
    useAppStore.setState({ raceEvents: [event as never] })

    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: `Calendar day ${date}` }))
    // Enter edit mode, then reset to a blank "New" form
    await user.click(screen.getByRole('button', { name: /Edit race event/i }))
    expect(screen.getByLabelText(/Distance/)).toHaveValue(120)
    await user.click(screen.getByRole('button', { name: /^New$/i }))
    expect(screen.getByRole('button', { name: /Add event/i })).toBeInTheDocument()

    // Close the editor entirely
    await user.click(screen.getByRole('button', { name: /Close race event editor/i }))
    expect(screen.queryByLabelText(/Distance/)).not.toBeInTheDocument()
  })

  it('overlays logged activities when showLoggedActivities is set', () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Ride',
          activityDate: rideDate,
          durationSeconds: 3600,
          planMatchStatus: 'unmatched',
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    expect(screen.getByText('1h 0m')).toBeInTheDocument()
    expect(screen.getByTitle(/Logged: ride · 1h 0m \(unplanned\)/)).toBeInTheDocument()
  })

  it('does not overlay logged activities without the prop', () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Ride',
          activityDate: rideDate,
          durationSeconds: 3600,
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    expect(screen.queryByText('1h 0m')).not.toBeInTheDocument()
  })

  it('marks a logged ride as matched when it is tied to a planned day', () => {
    const date = monthDate(0, 12)
    useAppStore.setState({
      trainingPlan: [makeDay(date, 'intervals')],
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Ride',
          activityDate: date,
          durationSeconds: 5400,
          planMatchStatus: 'auto_matched',
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    expect(screen.getByTitle(/matched to plan/)).toBeInTheDocument()
  })

  it('falls back to the sport label for a logged ride without a duration', () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Virtual_Ride',
          activityDate: rideDate,
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    // No duration -> shows the sport label, and the title omits the duration part.
    expect(screen.getByText('virtual ride')).toBeInTheDocument()
    expect(screen.getByTitle('Logged: virtual ride (unplanned)')).toBeInTheDocument()
  })

  it('formats a sub-hour logged ride in minutes', () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Ride',
          activityDate: rideDate,
          durationSeconds: 1800,
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    expect(screen.getByText('30m')).toBeInTheDocument()
  })

  it('labels ambiguous, manually matched, and unmatched-with-plan rides', () => {
    const ambiguousDate = monthDate(0, 10)
    const manualDate = monthDate(0, 11)
    const unmatchedDate = monthDate(0, 12)
    useAppStore.setState({
      trainingPlan: [makeDay(unmatchedDate, 'endurance')],
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          externalActivityId: 'ext-1',
          sportType: 'Ride',
          activityDate: ambiguousDate,
          durationSeconds: 3600,
          planMatchStatus: 'ambiguous',
        } as never,
        {
          stravaActivityId: 2,
          sportType: 'Ride',
          activityDate: manualDate,
          durationSeconds: 3600,
          planMatchStatus: 'manual_matched',
        } as never,
        {
          stravaActivityId: 3,
          sportType: 'Ride',
          activityDate: unmatchedDate,
          durationSeconds: 3600,
          planMatchStatus: undefined,
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    expect(screen.getByTitle(/ambiguous match/)).toBeInTheDocument()
    expect(screen.getByTitle(/matched to plan/)).toBeInTheDocument()
    // A ride with no match status but a plan on that day reads as "unmatched".
    expect(screen.getByTitle(/\(unmatched\)/)).toBeInTheDocument()
  })

  it('collapses more than two logged rides on a day into an overflow count', () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [1, 2, 3, 4].map(
        (id) =>
          ({
            stravaActivityId: id,
            sportType: 'Ride',
            activityDate: rideDate,
            durationSeconds: 3600,
          }) as never
      ),
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    expect(screen.getByText('+2 more')).toBeInTheDocument()
  })

  it('flags a planned past day with nothing logged as missed', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-15T12:00:00'))
    try {
      useAppStore.setState({
        trainingPlan: [makeDay('2026-07-10', 'endurance')],
        rideMetricsHistory: [],
      })

      render(
        <MemoryRouter>
          <TrainingCalendar showLoggedActivities />
        </MemoryRouter>
      )

      expect(screen.getByText('missed')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('navigates to the workout day when a logged-only day is clicked', async () => {
    const rideDate = monthDate(0, 12)
    useAppStore.setState({
      rideMetricsHistory: [
        {
          stravaActivityId: 1,
          sportType: 'Ride',
          activityDate: rideDate,
          durationSeconds: 3600,
        } as never,
      ],
    })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    await userEvent.click(screen.getByRole('button', { name: `Calendar day ${rideDate}` }))
    expect(mockNavigate).toHaveBeenCalledWith(`/workout/${rideDate}`)
  })

  it('navigates to the previous month', async () => {
    render(
      <MemoryRouter>
        <TrainingCalendar editableEvents />
      </MemoryRouter>
    )
    const user = userEvent.setup()
    // Should not throw and keeps the calendar grid rendered
    await user.click(screen.getByRole('button', { name: /Previous month/i }))
    expect(screen.getByText('Mon')).toBeInTheDocument()
  })

  describe('planned-day weather (#495)', () => {
    function tomorrowIso(): string {
      return formatIsoDate(new Date(Date.now() + 24 * 60 * 60 * 1000))
    }

    it('shows the forecast temperature on an upcoming planned day', () => {
      const date = tomorrowIso()
      useAppStore.setState({
        trainingPlan: [makeDay(date, 'intervals')],
        weatherForecast: {
          [date]: {
            date,
            condition: 'clear',
            temperatureMaxC: 38.4,
            temperatureMinC: 23.0,
            loadFlag: 'very_hot',
          },
        },
      })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.getByText('38\u00b0C')).toBeInTheDocument()
    })

    it('shows no forecast on a day that has no planned session', () => {
      const date = tomorrowIso()
      useAppStore.setState({
        trainingPlan: [],
        weatherForecast: {
          [date]: { date, condition: 'clear', temperatureMaxC: 38.4 },
        },
      })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.queryByText('38\u00b0C')).not.toBeInTheDocument()
    })

    it('shows the completion check instead of the forecast on a done day', () => {
      const date = tomorrowIso()
      useAppStore.setState({
        trainingPlan: [{ ...makeDay(date), completed: true }],
        weatherForecast: {
          [date]: { date, condition: 'clear', temperatureMaxC: 38.4 },
        },
      })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.queryByText('38\u00b0C')).not.toBeInTheDocument()
    })

    it('shows no forecast on a planned day already in the past', () => {
      const date = formatIsoDate(new Date(Date.now() - 2 * 24 * 60 * 60 * 1000))
      useAppStore.setState({
        trainingPlan: [makeDay(date)],
        weatherForecast: {
          [date]: { date, condition: 'clear', temperatureMaxC: 38.4 },
        },
      })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.queryByText('38\u00b0C')).not.toBeInTheDocument()
    })
  })
  // ---------------------------------------------------------------------
  // Two-a-days (#496)
  // ---------------------------------------------------------------------

  describe('multiple sessions per day', () => {
    function twoADay(date: string): TrainingDay[] {
      return [
        {
          ...makeDay(date, 'recovery'),
          slot: 0,
          timeOfDay: 'am',
          title: 'Morning yoga',
          durationMinutes: 30,
        },
        {
          ...makeDay(date, 'intervals'),
          slot: 1,
          timeOfDay: 'pm',
          title: 'Evening intervals',
          durationMinutes: 90,
        },
      ]
    }

    it('stacks both sessions in the day cell instead of showing only the first', () => {
      const date = monthDate(0, 14)
      useAppStore.setState({ trainingPlan: twoADay(date) })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.getByText('Morning yoga')).toBeInTheDocument()
      expect(screen.getByText('Evening intervals')).toBeInTheDocument()
      expect(screen.getByText('AM')).toBeInTheDocument()
      expect(screen.getByText('PM')).toBeInTheDocument()
    })

    it('tints the cell by the hardest session of the day', () => {
      const date = monthDate(0, 14)
      useAppStore.setState({ trainingPlan: twoADay(date) })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      // An easy AM spin next to a PM interval block must read as an interval
      // day, not a recovery day.
      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.className).toContain('bg-red-50')
    })

    it('marks the day complete only once every session is done', () => {
      const date = monthDate(0, 14)
      const [am, pm] = twoADay(date)
      useAppStore.setState({ trainingPlan: [{ ...am, completed: true }, pm] })

      const { rerender } = render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )
      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.querySelectorAll('.lucide-circle-check-big').length).toBe(1)

      useAppStore.setState({
        trainingPlan: [{ ...am, completed: true }, { ...pm, completed: true }],
      })
      rerender(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )
      // Both the per-session tick and the whole-day tick are now shown.
      expect(
        screen.getByLabelText(`Calendar day ${date}`).querySelectorAll('.lucide-circle-check-big')
          .length
      ).toBe(3)
    })

    it('adds no session label to an ordinary single-workout day', () => {
      const date = monthDate(0, 14)
      useAppStore.setState({ trainingPlan: [makeDay(date, 'endurance')] })

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.queryByText('AM')).not.toBeInTheDocument()
      expect(screen.queryByText('1/1')).not.toBeInTheDocument()
    })
  })
})
