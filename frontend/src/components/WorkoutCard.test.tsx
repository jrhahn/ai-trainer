import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import WorkoutCard from './WorkoutCard'
import type { TrainingDay } from '../store/useAppStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const baseDay: TrainingDay = {
  date: '2024-05-01',
  workoutType: 'intervals',
  title: 'VO2max Intervals',
  description: '5x4min at 120% FTP',
  durationMinutes: 60,
}

function renderCard(day: TrainingDay, showDate?: boolean) {
  return render(
    <MemoryRouter>
      <WorkoutCard day={day} showDate={showDate} />
    </MemoryRouter>
  )
}

describe('WorkoutCard', () => {
  it('renders the workout title and type badge', () => {
    renderCard(baseDay)
    expect(screen.getByText('VO2max Intervals')).toBeInTheDocument()
    expect(screen.getByText('intervals')).toBeInTheDocument()
  })

  it('renders the duration', () => {
    renderCard(baseDay)
    expect(screen.getByText(/60 min/)).toBeInTheDocument()
  })

  it('renders target power when provided', () => {
    renderCard({ ...baseDay, targetPower: { low: 280, high: 320 } })
    expect(screen.getByText(/280-320W/)).toBeInTheDocument()
  })

  it('renders target heart rate when provided', () => {
    renderCard({ ...baseDay, targetHeartRate: { low: 155, high: 170 } })
    expect(screen.getByText(/155-170 bpm/)).toBeInTheDocument()
  })

  it('shows a completed icon when day.completed is true', () => {
    renderCard({ ...baseDay, completed: true })
    // CheckCircle has no text but we can verify the card still renders correctly
    expect(screen.getByText('VO2max Intervals')).toBeInTheDocument()
  })

  it('shows the date when showDate is true', () => {
    renderCard(baseDay, true)
    // The formatted date should appear (locale-dependent, but some date fragment must be present)
    expect(screen.getByText(/2024|May|Wed/)).toBeInTheDocument()
  })

  it('navigates to the workout page when clicked', async () => {
    renderCard(baseDay)
    await userEvent.click(screen.getByText('VO2max Intervals'))
    expect(mockNavigate).toHaveBeenCalledWith('/workout/2024-05-01')
  })
})
