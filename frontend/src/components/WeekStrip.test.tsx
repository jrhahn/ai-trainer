import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { addDays, startOfWeek } from 'date-fns'
import WeekStrip from './WeekStrip'
import type { TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

const monday = startOfWeek(new Date(), { weekStartsOn: 1 })
const dayOfWeek = (index: number) => formatLocalDate(addDays(monday, index))

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
  it('always shows seven days, planned or not', () => {
    renderStrip([plan(dayOfWeek(0)), plan(dayOfWeek(3), { workoutType: 'rest' })])

    // Seven day chips; "Show more" is absent without a handler.
    expect(screen.getAllByRole('button')).toHaveLength(7)
    expect(screen.getAllByText('—')).toHaveLength(5)
  })

  it('selects a day instead of navigating away from the dashboard', async () => {
    const onSelect = vi.fn()
    const date = dayOfWeek(2)
    renderStrip([plan(date, { workoutType: 'intervals' })], { onSelect })

    // Nothing in the strip is a link any more — that was the whole bug (#634).
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: /intervals/i }))

    expect(onSelect).toHaveBeenCalledWith(date)
  })

  it('lets an unplanned day be selected too', async () => {
    const onSelect = vi.fn()
    const empty = dayOfWeek(5)
    const weekday = new Date(`${empty}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([plan(dayOfWeek(0))], { onSelect })

    await userEvent.click(screen.getByRole('button', { name: `${weekday} — nothing planned` }))

    expect(onSelect).toHaveBeenCalledWith(empty)
  })

  it('spells out for a screen reader what the layout says to the eye', () => {
    // The chip itself only reads "Tu", a coloured dot and "Recovery".
    const date = dayOfWeek(1)
    const weekday = new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([plan(date, { workoutType: 'recovery' })])

    expect(screen.getByRole('button', { name: `${weekday} — Recovery` })).toBeInTheDocument()
  })

  it('marks which day the hero is showing', () => {
    const selected = dayOfWeek(4)
    renderStrip([plan(dayOfWeek(4)), plan(dayOfWeek(5))], { selectedDate: selected })

    const pressed = screen.getAllByRole('button').filter(
      (chip) => chip.getAttribute('aria-pressed') === 'true'
    )
    expect(pressed).toHaveLength(1)
  })

  it('keeps today as the reference point while another day is selected', () => {
    const today = formatLocalDate(new Date())
    // Pick a day that is not today, whichever weekday the suite runs on.
    const other = today === dayOfWeek(0) ? dayOfWeek(1) : dayOfWeek(0)
    renderStrip([plan(today), plan(other)], { selectedDate: other })

    const current = screen.getAllByRole('button').filter(
      (chip) => chip.getAttribute('aria-current') === 'date'
    )
    expect(current).toHaveLength(1)
    expect(current[0].getAttribute('aria-pressed')).toBe('false')
  })

  it('labels a day by its session type', () => {
    renderStrip([plan(dayOfWeek(1), { workoutType: 'recovery' })])

    expect(screen.getByText('Recovery')).toBeInTheDocument()
  })

  it('offers the month behind "Show more"', async () => {
    const onShowMore = vi.fn()
    renderStrip([plan(dayOfWeek(0))], { onShowMore })

    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))

    expect(onShowMore).toHaveBeenCalledTimes(1)
  })

  it('leaves the header bare where there is nothing more to show', () => {
    renderStrip([plan(dayOfWeek(0))])

    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
  })
})
