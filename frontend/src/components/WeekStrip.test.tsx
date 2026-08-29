import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
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

function renderStrip(days: TrainingDay[], onShowMore?: () => void) {
  render(
    <MemoryRouter>
      <WeekStrip plan={days} onShowMore={onShowMore} />
    </MemoryRouter>
  )
}

describe('WeekStrip', () => {
  it('always shows seven days, planned or not', () => {
    renderStrip([plan(dayOfWeek(0)), plan(dayOfWeek(3), { workoutType: 'rest' })])

    // Two planned days link; the empty ones still occupy their column.
    expect(screen.getAllByRole('link')).toHaveLength(2)
    expect(screen.getAllByText('—')).toHaveLength(5)
  })

  it('links each planned day to its session', () => {
    const date = dayOfWeek(2)
    renderStrip([plan(date, { workoutType: 'intervals' })])

    expect(screen.getByRole('link')).toHaveAttribute('href', `/workout/${date}`)
  })

  it('labels a day by its session type', () => {
    renderStrip([plan(dayOfWeek(1), { workoutType: 'recovery' })])

    expect(screen.getByText('Recovery')).toBeInTheDocument()
  })

  it('marks today so the week has a reference point', () => {
    const today = formatLocalDate(new Date())
    renderStrip([plan(today)])

    expect(screen.getByRole('link')).toHaveAttribute('aria-current', 'date')
  })

  it('offers the month behind "Show more"', async () => {
    const onShowMore = vi.fn()
    renderStrip([plan(dayOfWeek(0))], onShowMore)

    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))

    expect(onShowMore).toHaveBeenCalledTimes(1)
  })

  it('leaves the header bare where there is nothing more to show', () => {
    renderStrip([plan(dayOfWeek(0))])

    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
  })
})
