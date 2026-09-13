import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { addDays } from 'date-fns'
import WeekStrip from './WeekStrip'
import type { TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

/** A day relative to today, which is where the strip is anchored since #672.
 *  The offsets the strip shows are -3 … +3. */
const dayOffset = (offset: number) => formatLocalDate(addDays(new Date(), offset))

const plan = (date: string, overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date,
  workoutType: 'endurance',
  title: 'Session',
  description: '',
  durationMinutes: 60,
  ...overrides,
})

function renderStrip(
  days: TrainingDay[],
  options: { selectedDate?: string; onSelect?: (date: string) => void; onShowMore?: () => void } = {}
) {
  render(
    <WeekStrip
      plan={days}
      selectedDate={options.selectedDate ?? formatLocalDate(new Date())}
      onSelect={options.onSelect ?? vi.fn()}
      onShowMore={options.onShowMore}
    />
  )
}

describe('WeekStrip', () => {
  afterEach(() => vi.useRealTimers())

  it('always shows seven days, planned or not', () => {
    renderStrip([plan(dayOffset(-3)), plan(dayOffset(0), { workoutType: 'rest' })])

    // Seven day chips; "Show more" is absent without a handler.
    expect(screen.getAllByRole('button')).toHaveLength(7)
    expect(screen.getAllByText('—')).toHaveLength(5)
  })

  it.each([
    ['Sunday', '2026-06-28T09:00:00'],
    ['Monday', '2026-06-29T09:00:00'],
    ['Wednesday', '2026-07-01T09:00:00'],
  ])('puts today in the middle on a %s', (_weekday, clock) => {
    // The old strip ran Monday–Sunday, so on a Sunday it showed six days of
    // history and no upcoming session at all (#672). The window is rolling now,
    // and the same three days sit either side whatever the weekday.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(clock))

    renderStrip(Array.from({ length: 7 }, (_, i) => plan(dayOffset(i - 3))))

    const chips = screen.getAllByRole('button')
    expect(chips).toHaveLength(7)
    expect(chips[3].getAttribute('aria-current')).toBe('date')
    expect(chips.filter((chip) => chip.getAttribute('aria-current') === 'date')).toHaveLength(1)
  })

  it('reaches three days back and three days forward', async () => {
    const onSelect = vi.fn()
    // A weekday name cannot tell the ends apart — day -4 and day +3 share one —
    // so the window is read off the chips' own order instead.
    renderStrip([plan(dayOffset(-4)), plan(dayOffset(4))], { onSelect })

    const chips = screen.getAllByRole('button')
    await userEvent.click(chips[0])
    expect(onSelect).toHaveBeenCalledWith(dayOffset(-3))

    await userEvent.click(chips[6])
    expect(onSelect).toHaveBeenCalledWith(dayOffset(3))

    // The two planned days sit just outside the window, so nothing is planned
    // inside it: seven empty chips.
    expect(screen.getAllByText('—')).toHaveLength(7)
  })

  it('selects a day instead of navigating away from the dashboard', async () => {
    const onSelect = vi.fn()
    const date = dayOffset(2)
    renderStrip([plan(date, { workoutType: 'intervals' })], { onSelect })

    // Nothing in the strip is a link any more — that was the whole bug (#634).
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: /intervals/i }))

    expect(onSelect).toHaveBeenCalledWith(date)
  })

  it('lets an unplanned day be selected too', async () => {
    const onSelect = vi.fn()
    const empty = dayOffset(1)
    const weekday = new Date(`${empty}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([plan(dayOffset(-2))], { onSelect })

    await userEvent.click(screen.getByRole('button', { name: `${weekday} — nothing planned` }))

    expect(onSelect).toHaveBeenCalledWith(empty)
  })

  it('spells out for a screen reader what the layout says to the eye', () => {
    // The chip itself only reads "Tu", a coloured dot and "Recovery".
    const date = dayOffset(-1)
    const weekday = new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([plan(date, { workoutType: 'recovery' })])

    expect(screen.getByRole('button', { name: `${weekday} — Recovery` })).toBeInTheDocument()
  })

  it('marks which day the hero is showing', () => {
    const selected = dayOffset(2)
    renderStrip([plan(dayOffset(2)), plan(dayOffset(3))], { selectedDate: selected })

    const pressed = screen.getAllByRole('button').filter(
      (chip) => chip.getAttribute('aria-pressed') === 'true'
    )
    expect(pressed).toHaveLength(1)
  })

  it('keeps today as the reference point while another day is selected', () => {
    const today = formatLocalDate(new Date())
    const other = dayOffset(-2)
    renderStrip([plan(today), plan(other)], { selectedDate: other })

    const current = screen.getAllByRole('button').filter(
      (chip) => chip.getAttribute('aria-current') === 'date'
    )
    expect(current).toHaveLength(1)
    expect(current[0].getAttribute('aria-pressed')).toBe('false')
  })

  it('does not swallow the second session of a two-a-day', () => {
    // A Map keyed by date keeps only the last entry — the same bug as
    // `plan.find()` in another shape (#645).
    const date = dayOffset(2)
    const weekday = new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([
      plan(date, { slot: 0, workoutType: 'intervals' }),
      plan(date, { slot: 1, workoutType: 'recovery' }),
    ])

    expect(
      screen.getByRole('button', { name: `${weekday} — Intervals, Recovery` })
    ).toBeInTheDocument()
    expect(screen.getByText('2 sessions')).toBeInTheDocument()
  })

  it('only ticks a two-a-day off once both halves are done', () => {
    const date = dayOffset(-3)
    renderStrip([
      plan(date, { slot: 0, completed: true }),
      plan(date, { slot: 1, completed: false }),
    ])

    expect(document.querySelectorAll('svg.text-emerald-500')).toHaveLength(0)
  })

  it('labels a day by its session type', () => {
    renderStrip([plan(dayOffset(1), { workoutType: 'recovery' })])

    expect(screen.getByText('Recovery')).toBeInTheDocument()
  })

  it('offers the month behind "Show more"', async () => {
    const onShowMore = vi.fn()
    renderStrip([plan(dayOffset(0))], { onShowMore })

    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))

    expect(onShowMore).toHaveBeenCalledTimes(1)
  })

  it('leaves the header bare where there is nothing more to show', () => {
    renderStrip([plan(dayOffset(0))])

    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
  })
})
