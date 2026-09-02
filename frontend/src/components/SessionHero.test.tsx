import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { addDays } from 'date-fns'
import SessionHero from './SessionHero'
import type { TrainingDay } from '../store/useAppStore'
import { useAppStore } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

const TODAY = formatLocalDate(new Date())
const TOMORROW = formatLocalDate(addDays(new Date(), 1))

const day = (overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date: TODAY,
  workoutType: 'intervals',
  title: '4 x 8 min threshold',
  description: '4 x 8 min at 265-280 W, 4 min easy between.',
  durationMinutes: 75,
  ...overrides,
})

function renderHero(
  props: { day?: TrainingDay; sessions?: TrainingDay[]; date?: string; done?: string[] } = {}
) {
  // `day: undefined` means "the plan has nothing"; omitting it means "the usual one".
  const sessions =
    props.sessions ?? ('day' in props ? (props.day ? [props.day] : []) : [day()])
  render(
    <MemoryRouter>
      <SessionHero
        sessions={sessions}
        date={props.date ?? TODAY}
        doneKeys={new Set(props.done ?? [])}
      />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('SessionHero', () => {
  it('names the session and what it asks for', () => {
    renderHero({ day: day({ targetPower: { low: 265, high: 280 } }) })

    expect(screen.getByRole('heading', { name: '4 x 8 min threshold' })).toBeInTheDocument()
    expect(screen.getByText(/4 x 8 min at 265-280 W/)).toBeInTheDocument()
    expect(screen.getByText('265–280 W')).toBeInTheDocument()
    expect(screen.getByText('1h 15m')).toBeInTheDocument()
  })

  it('shows the instructions without a second click', () => {
    renderHero({
      day: day({
        keyFocusPoints: ['Keep cadence smooth and high'],
        workoutPurpose: 'Raises the power you can hold for an hour.',
      }),
    })

    // "Show details" and its overlay are gone: the card is the details (#634).
    expect(screen.queryByRole('button', { name: /show details/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('Keep cadence smooth and high')).toBeInTheDocument()
    expect(screen.getByText('Raises the power you can hold for an hour.')).toBeInTheDocument()
  })

  it('shows the interval breakdown inline', () => {
    renderHero({ day: day({ intervals: [{ duration: 480, power: 270, rest: 240 }] }) })

    expect(screen.getByText('Interval Breakdown')).toBeInTheDocument()
    expect(screen.getByText('8:00')).toBeInTheDocument()
    expect(screen.getByText('270W')).toBeInTheDocument()
  })

  it('states the duration once, not twice', () => {
    // The hero's own summary row replaces WorkoutDetails' plainer one.
    renderHero()

    expect(screen.getAllByText('1h 15m')).toHaveLength(1)
  })

  it('offers no way off the card', () => {
    // The card is the session; the workout page is reached from the calendar
    // behind "Show more" instead.
    renderHero({ day: day({ date: '2026-08-26' }), date: '2026-08-26' })

    expect(screen.queryAllByRole('link')).toHaveLength(0)
    expect(screen.queryByText(/open the full session page/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/review and log this session/i)).not.toBeInTheDocument()
  })

  it('counts a logged ride as done even when the plan day was never ticked', () => {
    renderHero({ day: day({ completed: false }), done: [`${TODAY}#0`] })

    expect(screen.getByText('Done')).toBeInTheDocument()
  })

  it('says which day it is showing', () => {
    renderHero({ day: day({ date: TOMORROW }), date: TOMORROW })

    const weekday = new Date(`${TOMORROW}T00:00:00`).toLocaleDateString(undefined, {
      weekday: 'long',
    })
    expect(screen.getByText(weekday)).toBeInTheDocument()
    expect(screen.queryByText('Today')).not.toBeInTheDocument()
  })

  it('reads "Today" only when it is', () => {
    renderHero()

    expect(screen.getByText('Today')).toBeInTheDocument()
  })

  it('drops the duration on a rest day rather than showing 0m', () => {
    renderHero({ day: day({ workoutType: 'rest', title: 'Rest day', durationMinutes: 0 }) })

    expect(screen.getByRole('heading', { name: 'Rest day' })).toBeInTheDocument()
    expect(screen.queryByText('0m')).not.toBeInTheDocument()
  })

  it('names the empty day rather than always blaming today', () => {
    renderHero({ day: undefined, date: TOMORROW })

    const weekday = new Date(`${TOMORROW}T00:00:00`).toLocaleDateString(undefined, {
      weekday: 'long',
    })
    expect(screen.getByText(new RegExp(`no session planned for ${weekday}`, 'i'))).toBeInTheDocument()
  })

  it('says so when the plan has nothing for today', () => {
    renderHero({ day: undefined })

    expect(screen.getByText(/no session planned for today/i)).toBeInTheDocument()
  })

  // -------------------------------------------------------------------------
  // Two-a-days (#645). The redesign took a single TrainingDay, so the caller
  // picked one with `plan.find()` — the pattern #496 removed — and the second
  // session vanished from the dashboard.
  // -------------------------------------------------------------------------

  const morning = day({ slot: 0, title: 'Morning intervals' })
  const evening = day({ slot: 1, title: 'Evening endurance', workoutType: 'endurance' })

  it('shows both sessions of a two-a-day', () => {
    renderHero({ sessions: [morning, evening] })

    expect(screen.getByRole('heading', { name: 'Morning intervals' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evening endurance' })).toBeInTheDocument()
  })

  it('labels each session so they cannot be confused', () => {
    renderHero({ sessions: [morning, evening] })

    expect(screen.getByText('1/2')).toBeInTheDocument()
    expect(screen.getByText('2/2')).toBeInTheDocument()
  })

  it('does not label a single session as one of several', () => {
    renderHero()

    expect(screen.queryByText('1/1')).not.toBeInTheDocument()
    expect(screen.queryByText(/^1\/\d$/)).not.toBeInTheDocument()
  })

  it('names the day once however many sessions it holds', () => {
    renderHero({ sessions: [morning, evening] })

    expect(screen.getAllByText('Today')).toHaveLength(1)
  })

  it('marks only the session that was actually ridden', () => {
    // One ride on a two-a-day must not tick both off.
    renderHero({ sessions: [morning, evening], done: [`${TODAY}#0`] })

    expect(screen.getAllByText('Done')).toHaveLength(1)
  })
})
