import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TodayCard from './TodayCard'
import type { TrainingDay } from '../store/useAppStore'

const TODAY = '2024-05-01'

const workoutDay: TrainingDay = {
  date: TODAY,
  workoutType: 'intervals',
  title: 'VO2max Intervals',
  description: '5x4min at 120% FTP',
  durationMinutes: 60,
}

function renderCard(today: string, plan: TrainingDay[]) {
  return render(
    <MemoryRouter>
      <TodayCard today={today} trainingPlan={plan} />
    </MemoryRouter>
  )
}

describe('TodayCard', () => {
  it('shows a rest-day card when today is a rest workout', () => {
    renderCard(TODAY, [{ ...workoutDay, workoutType: 'rest' }])
    expect(screen.getByText('Rest day')).toBeInTheDocument()
    expect(screen.getByText('Recovery & rest')).toBeInTheDocument()
  })

  it('shows the empty hint when no workout is scheduled for today', () => {
    renderCard(TODAY, [{ ...workoutDay, date: '2024-04-30' }])
    expect(screen.getByText('No workout scheduled for today.')).toBeInTheDocument()
  })

  it('renders the workout title, type and duration', () => {
    renderCard(TODAY, [workoutDay])
    expect(screen.getByText('VO2max Intervals')).toBeInTheDocument()
    expect(screen.getByText('intervals')).toBeInTheDocument()
    expect(screen.getByText('1h')).toBeInTheDocument()
  })

  it('links to the workout detail page', () => {
    renderCard(TODAY, [workoutDay])
    expect(screen.getByRole('link')).toHaveAttribute('href', `/workout/${TODAY}`)
  })

  it('renders target power and heart-rate ranges when provided', () => {
    renderCard(TODAY, [
      {
        ...workoutDay,
        targetPower: { low: 280, high: 320 },
        targetHeartRate: { low: 155, high: 170 },
      },
    ])
    expect(screen.getByText('280–320 W')).toBeInTheDocument()
    expect(screen.getByText('155–170 bpm')).toBeInTheDocument()
  })

  it('shows the completed indicator and "View details" CTA when done', () => {
    renderCard(TODAY, [{ ...workoutDay, completed: true }])
    expect(screen.getByText('✓ Done')).toBeInTheDocument()
    expect(screen.getByText('View details')).toBeInTheDocument()
  })

  it('shows the log CTA when the workout is not yet completed', () => {
    renderCard(TODAY, [workoutDay])
    expect(screen.getByText('View & log workout')).toBeInTheDocument()
  })
})
