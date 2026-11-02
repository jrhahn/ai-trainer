import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TrainingCalendar from './TrainingCalendar'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'
import { setPlan } from '../queries/plan'

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

/** A calendar day from today, at noon: `Date.now()` ± 24 h is not a day on
 *  the 23- or 25-hour day a clock change makes. */
function dayFromToday(days: number): string {
  const now = new Date()
  return formatIsoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, 12))
}

function monthDate(monthOffset: number, day: number): string {
  const now = new Date()
  return formatIsoDate(new Date(now.getFullYear(), now.getMonth() + monthOffset, day, 12))
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  setPlan([])
  useAppStore.setState({
    authToken: 'tok-123',
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
    setPlan([makeDay(workoutDate, 'intervals')])

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    expect(screen.getByText('intervals workout')).toBeInTheDocument()
  })

  it('navigates to the workout page when a day cell is clicked', async () => {
    const workoutDate = monthDate(0, 13)
    setPlan([makeDay(workoutDate)])

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    await userEvent.click(screen.getByText('endurance workout'))
    expect(mockNavigate).toHaveBeenCalledWith(`/workout/${workoutDate}`)
  })

  it('renders empty cells for days not in the plan', () => {
    setPlan([makeDay(monthDate(0, 13))])

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

  it('keeps the accent for the ride that waits for an answer, and grey for the rest (#574)', () => {
    const rides = [
      { stravaActivityId: 1, sportType: 'Ride', activityDate: monthDate(0, 12), durationSeconds: 3600, planMatchStatus: 'ambiguous' },
      { stravaActivityId: 2, sportType: 'Ride', activityDate: monthDate(0, 13), durationSeconds: 3600, planMatchStatus: 'auto_matched' },
      { stravaActivityId: 3, sportType: 'Ride', activityDate: monthDate(0, 14), durationSeconds: 3600, planMatchStatus: 'unmatched' },
    ]
    useAppStore.setState({ rideMetricsHistory: rides as never })

    render(
      <MemoryRouter>
        <TrainingCalendar showLoggedActivities />
      </MemoryRouter>
    )

    const dot = (title: RegExp) => screen.getByTitle(title).querySelector('span')
    expect(dot(/\(ambiguous match\)/)).toHaveClass('bg-amber-500')
    expect(dot(/\(matched to plan\)/)).toHaveClass('bg-gray-400')
    expect(dot(/\(unplanned\)/)).toHaveClass('bg-gray-300')
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
    setPlan([makeDay(date, 'intervals')])
    useAppStore.setState({
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
    setPlan([makeDay(unmatchedDate, 'endurance')])
    useAppStore.setState({
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
      setPlan([makeDay('2026-07-10', 'endurance')])
      useAppStore.setState({
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
      return dayFromToday(1)
    }

    it('shows the forecast temperature on an upcoming planned day', () => {
      const date = tomorrowIso()
      setPlan([makeDay(date, 'intervals')])
      useAppStore.setState({
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
      setPlan([])
      useAppStore.setState({
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
      setPlan([{ ...makeDay(date), completed: true }])
      useAppStore.setState({
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
      const date = dayFromToday(-2)
      setPlan([makeDay(date)])
      useAppStore.setState({
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
      setPlan(twoADay(date))

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

    it('draws a training day white, whichever session is the hardest', () => {
      const date = monthDate(0, 14)
      setPlan(twoADay(date))

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      // One look for every training day since the owner's review of 2026-10-24
      // ("kein Kunterbunt"): the icons and titles say the type.
      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.className).toContain('bg-white')
      expect(cell.className).not.toMatch(/bg-(red|blue|orange|purple|green|teal)-50/)
    })

    it('marks the day complete only once every session is done', () => {
      const date = monthDate(0, 14)
      const [am, pm] = twoADay(date)
      setPlan([{ ...am, completed: true }, pm])

      const { rerender } = render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )
      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.querySelectorAll('.lucide-check').length).toBe(1)

      setPlan([{ ...am, completed: true }, { ...pm, completed: true }])
      rerender(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )
      // Both the per-session tick and the whole-day tick are now shown.
      expect(
        screen.getByLabelText(`Calendar day ${date}`).querySelectorAll('.lucide-check')
          .length
      ).toBe(3)
    })

    it('adds no session label to an ordinary single-workout day', () => {
      const date = monthDate(0, 14)
      setPlan([makeDay(date, 'endurance')])

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      expect(screen.queryByText('AM')).not.toBeInTheDocument()
      expect(screen.queryByText('1/1')).not.toBeInTheDocument()
    })
  })

  describe('sport (#710)', () => {
    it('does not draw a bicycle on a planned run', () => {
      const date = monthDate(0, 15)
      setPlan([{ ...makeDay(date, 'endurance'), sport: 'running' }])

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      const cell = screen.getByLabelText(`Calendar day ${date}`)
      // By the icon in the cell, not by a glyph in its text: since
      // ai-trainer-ops#51.7 there is no glyph, and `textContent` would be
      // equally empty of "🚴" on a cell that drew nothing at all. The rule
      // itself is covered in `utils/sessionIcon.test.ts`; this is the wiring.
      expect(cell.querySelector('.lucide-footprints')).not.toBeNull()
      expect(cell.querySelector('.lucide-bike')).toBeNull()
      // Named as well as drawn: an icon is ambiguous at this size.
      expect(screen.getByText('Running')).toBeInTheDocument()
    })

    it('leaves a cycling day exactly as it was', () => {
      const date = monthDate(0, 16)
      setPlan([makeDay(date, 'endurance')])

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.querySelector('.lucide-bike')).not.toBeNull()
      expect(screen.queryByText('Cycling')).not.toBeInTheDocument()
    })

    it('draws no bicycle for a sport it has no icon for', () => {
      // A sport outside the three the planner can write still reaches storage —
      // the persist gate keeps an athlete's own edit rather than relabelling it
      // as cycling — and drawing a bike for it is the bug this all fixes.
      const date = monthDate(0, 18)
      setPlan([{ ...makeDay(date, 'endurance'), sport: 'swim' }])

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.querySelector('.lucide-bike')).toBeNull()
      // Positively too: "no bicycle" is also true of a cell that drew nothing.
      expect(cell.querySelector('.lucide-timer')).not.toBeNull()
      expect(screen.getByText('Swim')).toBeInTheDocument()
    })

    it('keeps a workout type that means the same in every sport', () => {
      // "intervals" is a purpose, not a sport: a run with intervals is still
      // drawn as intervals, with the same icon the week strip uses.
      const date = monthDate(0, 17)
      setPlan([{ ...makeDay(date, 'intervals'), sport: 'running' }])

      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      const cell = screen.getByLabelText(`Calendar day ${date}`)
      expect(cell.querySelector('.lucide-zap')).not.toBeNull()
      expect(cell.querySelector('.lucide-footprints'), 'the sport overrode a purpose').toBeNull()
    })
  })
})

describe('TrainingCalendar in German (ai-trainer-ops#104)', () => {
  it('names the month and the weekdays in the interface language, not the browser default', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>
      )

      // jsdom's browser speaks en-US, so the month only reads German from the interface language.
      const month = new Date().toLocaleDateString('de-DE', { month: 'long', year: 'numeric' })
      expect(screen.getByText(month)).toBeInTheDocument()
      expect(screen.getByText('Mo')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Heute' })).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})

describe('TrainingCalendar on a phone (ai-trainer-ops#338 A7)', () => {
  function asPhone(phone: boolean) {
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: phone, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    )
  }

  it('lists the days with their whole titles instead of a seven-column grid', async () => {
    asPhone(true)
    try {
      const date = monthDate(0, 15)
      setPlan([{ ...makeDay(date), title: 'Lockere Grundlagenausfahrt mit Trittfrequenz' }])
      useAppStore.setState({ uiLanguage: 'en', raceEvents: [] })
      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>,
      )

      const list = screen.getByRole('list')
      expect(list).toHaveTextContent('Lockere Grundlagenausfahrt mit Trittfrequenz')
      // Only the days with something on them: one row here.
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
      expect(screen.queryByRole('button', { name: /^Calendar day/ })).not.toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: /Lockere Grundlagenausfahrt/ }))
      expect(mockNavigate).toHaveBeenCalledWith(`/workout/${date}`)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('keeps the month grid on a wider screen', () => {
    asPhone(false)
    try {
      setPlan([makeDay(monthDate(0, 15))])
      render(
        <MemoryRouter>
          <TrainingCalendar />
        </MemoryRouter>,
      )

      expect(screen.queryByRole('list')).not.toBeInTheDocument()
      expect(screen.getAllByRole('button', { name: /^Calendar day/ }).length).toBeGreaterThanOrEqual(28)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
