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

  it('names all five perceived effort levels', () => {
    // By accessible name, not by `getByText` (ai-trainer-ops#51.5). The words
    // left the buttons' faces — five of them never fit the dialog, which is
    // `max-w-md` at every width — but each button is still *named* for its
    // level, which is the property that matters and the one `getByText` was not
    // checking. It would have passed on five loose labels beside five unnamed
    // buttons.
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)
    for (const [level, word] of [
      [1, 'Easy'],
      [2, 'Moderate'],
      [3, 'Hard'],
      [4, 'Very Hard'],
      [5, 'Max'],
    ] as const) {
      expect(screen.getByRole('button', { name: `${level} of 5 — ${word}` })).toBeInTheDocument()
    }
  })

  it('says which effort is chosen, in words and to a screen reader', () => {
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)

    // The default, 3. `aria-pressed` is the whole of the answer for a screen
    // reader: the chosen button is otherwise only amber.
    expect(screen.getByRole('button', { name: '3 of 5 — Hard' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByRole('button', { name: '1 of 5 — Easy' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
    // And the current choice is spelled out beside the field label, which is
    // what replaced the word under each button.
    expect(screen.getByText('Hard')).toBeInTheDocument()
  })

  it('moves the chosen effort when another level is picked', async () => {
    render(<WorkoutFeedbackForm day={baseDay} onSubmit={vi.fn()} onCancel={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: '5 of 5 — Max' }))

    expect(screen.getByRole('button', { name: '5 of 5 — Max' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByRole('button', { name: '3 of 5 — Hard' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
    // Exactly one, so the selection moved rather than spread.
    expect(
      screen.getAllByRole('button', { pressed: true }),
      'two efforts chosen at once'
    ).toHaveLength(1)
  })

  it('words the effort scale for a lifter on a strength session', () => {
    // The scale the duplicated copy in `WorkoutPage` did not have, which is how
    // "Controlled" in the picker became "Moderate" in the log one screen later.
    render(
      <WorkoutFeedbackForm
        day={{ ...baseDay, workoutType: 'strength' }}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: '2 of 5 — Controlled' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '3 of 5 — Challenging' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Moderate/ })).not.toBeInTheDocument()
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
