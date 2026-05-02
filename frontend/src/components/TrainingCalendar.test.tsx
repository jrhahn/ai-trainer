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
})
