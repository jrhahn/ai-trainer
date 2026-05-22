import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import WorkoutFeedbackForm from './WorkoutFeedbackForm'
import type { TrainingDay } from '../store/useAppStore'

const baseDay: TrainingDay = {
  date: '2024-05-01',
  workoutType: 'intervals',
  title: 'VO2max Intervals',
  description: '5x4min at 120% FTP',
  durationMinutes: 60,
}

describe('WorkoutFeedbackForm', () => {
  it('renders all form fields', () => {
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText('Log Workout')).toBeInTheDocument()
    expect(screen.getByText(/Duration \(minutes\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Avg Power/i)).toBeInTheDocument()
    expect(screen.getByText(/Avg HR/i)).toBeInTheDocument()
    expect(screen.getByText(/Perceived Effort/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save workout/i })).toBeInTheDocument()
  })

  it('pre-fills the duration with the planned duration', () => {
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)
    const durationInput = screen.getByDisplayValue('60')
    expect(durationInput).toBeInTheDocument()
  })

  it('calls onCancel when the Cancel button is clicked', async () => {
    const onCancel = vi.fn()
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={onCancel} />)
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('calls onSubmit with the form data when the form is submitted', async () => {
    const onSubmit = vi.fn()
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={onSubmit} onCancel={vi.fn()} />)

    // Change the effort level to 4
    await userEvent.click(screen.getAllByRole('button').find((b) => b.textContent?.includes('4'))!)

    // Fill in notes
    await userEvent.type(screen.getByPlaceholderText(/how did it feel/i), 'Great session!')

    await userEvent.click(screen.getByRole('button', { name: /save workout/i }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    const [feedback] = onSubmit.mock.calls[0]
    expect(feedback.actualDurationMinutes).toBe(60)
    expect(feedback.perceivedEffort).toBe(4)
    expect(feedback.notes).toBe('Great session!')
    expect(feedback.completedAt).toBeTruthy()
  })

  it('includes optional power and HR fields when filled in', async () => {
    const onSubmit = vi.fn()
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={onSubmit} onCancel={vi.fn()} />)

    // Spinbuttons in order: Duration (0), Avg Power (1), Avg HR (2), Peak Power (3)
    const avgPowerInput = screen.getAllByRole('spinbutton')[1]
    await userEvent.type(avgPowerInput, '230')

    await userEvent.click(screen.getByRole('button', { name: /save workout/i }))

    const [feedback] = onSubmit.mock.calls[0]
    expect(feedback.averagePower).toBe(230)
  })

  it('shows all five perceived effort buttons with labels', () => {
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText('Easy')).toBeInTheDocument()
    expect(screen.getByText('Moderate')).toBeInTheDocument()
    expect(screen.getByText('Hard')).toBeInTheDocument()
    expect(screen.getByText('Very Hard')).toBeInTheDocument()
    expect(screen.getByText('Max')).toBeInTheDocument()
  })

  it('uses strength-specific feedback fields for strength sessions', () => {
    render(
      <WorkoutFeedbackForm
        day={{ ...baseDay, workoutType: 'strength', title: 'Gym Strength' }}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText('Log Strength Session')).toBeInTheDocument()
    expect(screen.queryByText(/Avg Power/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Peak Power/i)).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Exercises, sets, load/i)).toBeInTheDocument()
  })
})
