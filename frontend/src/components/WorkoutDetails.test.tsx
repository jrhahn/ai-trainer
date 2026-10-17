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

  it('notes a planning rule the day still breaks (ai-trainer-ops#87)', () => {
    renderDetails({ priorNotes: ['back-to-back-hard'] })

    expect(screen.getByTestId('prior-note')).toHaveTextContent(
      'Planning rule not met: two hard days in a row.',
    )
  })

  it('notes a week over the stated hours (ai-trainer-ops#116)', () => {
    renderDetails({ priorNotes: ['stated-hours'] })

    expect(screen.getByTestId('prior-note')).toHaveTextContent(
      'Planning rule not met: the week holds more hours than you said you have.',
    )
  })

  it('notes intensity in a stated illness (ai-trainer-ops#112)', () => {
    renderDetails({ priorNotes: ['illness', 'lasting-tiredness'] })

    expect(screen.getByTestId('prior-note')).toHaveTextContent(
      'Planning rule not met: intensity while you are ill or just getting back from it; ' +
        'intensity while you have been tired for days.',
    )
  })

  it('shows no note for a day that breaks no rule', () => {
    renderDetails()

    expect(screen.queryByTestId('prior-note')).not.toBeInTheDocument()
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

  describe('strength prescriptions (#714)', () => {
    it('prescribes reps in reserve rather than a weight', () => {
      // The whole point of the unit: the plan cannot know what 100 kg will feel
      // like on a Thursday, so "RIR 2" is an instruction the athlete can follow
      // honestly on a good day and a bad one.
      renderDetails({
        workoutType: 'strength',
        sport: 'strength',
        strengthExercises: [{ exercise: 'back squat', sets: 3, reps: 5, rir: 2 }],
      })

      expect(screen.getByText('back squat')).toBeInTheDocument()
      expect(screen.getByText('3 × 5')).toBeInTheDocument()
      expect(screen.getByText('RIR 2')).toBeInTheDocument()
    })

    it('shows a percentage block as a percentage', () => {
      renderDetails({
        workoutType: 'strength',
        strengthExercises: [
          { exercise: 'bench press', sets: 4, reps: 6, percentE1rm: 77.5 },
        ],
      })

      expect(screen.getByText('@ 78%')).toBeInTheDocument()
    })

    it('says so when an exercise prescribes no intensity', () => {
      // "3x12 push-ups" is a legitimate volume prescription. A blank cell would
      // read as a value that failed to load.
      renderDetails({
        workoutType: 'strength',
        strengthExercises: [{ exercise: 'push-up', sets: 3, reps: 12 }],
      })

      expect(screen.getByText('—')).toBeInTheDocument()
    })

    it('leaves the lifts table out of a ride', () => {
      renderDetails({ intervals: [{ duration: 720, power: 260, rest: 240 }] })

      expect(screen.queryByRole('heading', { name: 'Lifts' })).not.toBeInTheDocument()
    })

    it('shows the lifts alongside intervals without either replacing the other', () => {
      // A session can carry both — the backend keeps them as separate fields
      // because an interval is a duration at a power and a set is reps at a
      // load, so neither table should hide the other.
      renderDetails({
        intervals: [{ duration: 720, power: 260, rest: 240 }],
        strengthExercises: [{ exercise: 'back squat', sets: 3, reps: 5, rir: 2 }],
      })

      expect(screen.getByRole('heading', { name: /Interval Breakdown/ })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: /Lifts/ })).toBeInTheDocument()
    })
  })
})
