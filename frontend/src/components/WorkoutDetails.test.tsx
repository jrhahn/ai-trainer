import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import WorkoutDetails from './WorkoutDetails'
import type { TrainingDay } from '../store/useAppStore'

const day = (overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date: '2026-08-26',
  workoutType: 'tempo',
  title: 'Sweet spot 3 x 12',
  description: 'Three blocks of 12 min at 250-265 W.',
  durationMinutes: 75,
  workoutPurpose: 'Clears lactate faster, so the same climbing pace costs you less.',
  keyFocusPoints: ['Keep cadence smooth and high', 'Stay strictly below 270 W'],
  ...overrides,
})

function renderDetails(overrides: Partial<TrainingDay> = {}) {
  render(
    <MemoryRouter>
      <WorkoutDetails day={day(overrides)} />
    </MemoryRouter>
  )
}

describe('WorkoutDetails', () => {
  it('puts the instructions above the goal', () => {
    renderDetails()

    const headings = screen.getAllByRole('heading').map((h) => h.textContent)
    expect(headings.indexOf('Key Focus Points')).toBeLessThan(
      headings.indexOf('Goal of this workout')
    )
  })

  it('follows the description straight into the focus points', () => {
    renderDetails()

    // Adjacency, not just order: nothing — the goal block least of all — may
    // come between what the session is and how to ride it.
    const description = screen.getByText('Three blocks of 12 min at 250-265 W.')
    const next = description.nextElementSibling

    expect(next).not.toBeNull()
    expect(within(next as HTMLElement).getByRole('heading').textContent).toBe('Key Focus Points')
  })

  it('states what the session earns rather than why it was scheduled', () => {
    renderDetails()

    expect(screen.getByText('Goal of this workout')).toBeInTheDocument()
    expect(screen.queryByText('Why this workout')).not.toBeInTheDocument()
  })

  it('lists every focus point', () => {
    renderDetails()

    expect(screen.getByText('Keep cadence smooth and high')).toBeInTheDocument()
    expect(screen.getByText('Stay strictly below 270 W')).toBeInTheDocument()
  })

  it('shows the targets it has and omits the ones it does not', () => {
    renderDetails({ targetPower: { low: 250, high: 265 } })

    expect(screen.getByText('250–265W')).toBeInTheDocument()
    expect(screen.queryByText(/bpm/)).not.toBeInTheDocument()
  })

  it('breaks the intervals down into minutes and seconds', () => {
    renderDetails({ intervals: [{ duration: 720, power: 260, rest: 240 }] })

    expect(screen.getByText('12:00')).toBeInTheDocument()
    expect(screen.getByText('260W')).toBeInTheDocument()
    expect(screen.getByText('4:00')).toBeInTheDocument()
  })

  it('offers a regenerate nudge only when the coaching detail is missing', () => {
    renderDetails({ workoutPurpose: undefined })

    expect(screen.getByRole('button', { name: 'Regenerate your plan' })).toBeInTheDocument()
  })

  it('leaves a rest day without the nudge', () => {
    renderDetails({ workoutType: 'rest', workoutPurpose: undefined })

    expect(screen.queryByRole('button', { name: 'Regenerate your plan' })).not.toBeInTheDocument()
  })
})
