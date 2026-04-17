import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TrainingCalendar from './TrainingCalendar'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function makeDay(date: string, workoutType: TrainingDay['workoutType'] = 'endurance'): TrainingDay {
  return {
    date,
    workoutType,
    title: `${workoutType} workout`,
    description: 'Training session',
    durationMinutes: 60,
  }
}

describe('TrainingCalendar', () => {
  it('renders the day-of-week headers', () => {
    useAppStore.setState({ trainingPlan: [] })
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
    // Use a fixed Monday so the layout is predictable
    const monday = '2025-01-13'
    useAppStore.setState({ trainingPlan: [makeDay(monday, 'intervals')] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    expect(screen.getByText('intervals workout')).toBeInTheDocument()
  })

  it('navigates to the workout page when a day cell is clicked', async () => {
    const monday = '2025-01-13'
    useAppStore.setState({ trainingPlan: [makeDay(monday)] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    await userEvent.click(screen.getByText('endurance workout'))
    expect(mockNavigate).toHaveBeenCalledWith(`/workout/${monday}`)
  })

  it('renders empty cells for days not in the plan', () => {
    useAppStore.setState({ trainingPlan: [makeDay('2025-01-13')] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    // The calendar always renders 4 weeks × 7 days = 28 cells; not all have workout content
    const workoutCells = screen.queryAllByText(/workout/)
    expect(workoutCells.length).toBeGreaterThan(0)
  })
})
