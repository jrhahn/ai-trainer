import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { addDays } from 'date-fns'
import WeekStrip from './WeekStrip'
import { useAppStore, type TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'
import { setProfile } from '../queries/profile'
import type { UserProfile } from '../types/domain'

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

  it('dims a title whose German is still being written (ai-trainer-ops#271)', () => {
    useAppStore.setState({
      uiLanguage: 'de',
      planTranslationsPending: true,
      planTranslations: { 'Endurance Ride': 'Grundlagenfahrt' },
    })
    try {
      renderStrip([plan(dayOffset(-1), { title: 'Endurance Ride' }), plan(dayOffset(0), { title: 'Recovery Spin' })])

      expect(screen.getByText('Recovery Spin')).toHaveClass('opacity-60')
      expect(screen.getByText('Grundlagenfahrt')).not.toHaveClass('opacity-60')
    } finally {
      useAppStore.setState({ uiLanguage: 'auto', planTranslationsPending: false, planTranslations: {} })
    }
  })

  it('always shows seven days, planned or not', () => {
    renderStrip([plan(dayOffset(-3)), plan(dayOffset(0), { workoutType: 'rest' })])

    // Seven day chips; "Calendar" is absent without a handler.
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
    // The chip itself reads "Tu", an icon, the title and the duration.
    const date = dayOffset(-1)
    const weekday = new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    renderStrip([plan(date, { workoutType: 'recovery' })])

    expect(screen.getByRole('button', { name: `${weekday} — Recovery, Session, 1h` })).toBeInTheDocument()
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

  it('draws a run as a runner and a ride as a bicycle (ai-trainer-ops#325)', () => {
    // On production a "Saturday Easy Run" (running, endurance) showed a bicycle.
    const dayIcon = (offset: number) => {
      const weekday = new Date(`${dayOffset(offset)}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
      const chip = screen.getByRole('button', { name: new RegExp(`^${weekday} — `) })
      return [...chip.querySelectorAll('svg')].map((svg) => svg.getAttribute('class'))
    }
    renderStrip([
      plan(dayOffset(-1), { sport: 'running', title: 'Easy Run' }),
      plan(dayOffset(0), { sport: 'cycling', title: 'Easy Ride' }),
      plan(dayOffset(1), { sport: 'strength', workoutType: 'strength', title: 'Gym' }),
      plan(dayOffset(2), { sport: 'running', workoutType: 'intervals', title: 'Track' }),
    ])

    expect(dayIcon(-1)).toEqual([expect.stringContaining('lucide-footprints')])
    expect(dayIcon(0)).toEqual([expect.stringContaining('lucide-bike')])
    expect(dayIcon(1)).toEqual([expect.stringContaining('lucide-dumbbell')])
    // A type that says what the session is for keeps its own icon in any sport.
    expect(dayIcon(2)).toEqual([expect.not.stringContaining('lucide-bike')])
    expect(dayIcon(2)).toEqual([expect.not.stringContaining('lucide-footprints')])
  })

  it("drops the title's weekday the chip already shows (ai-trainer-ops#324)", () => {
    const date = dayOffset(1)
    const english = new Date(`${date}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long' })
    renderStrip([plan(date, { title: `${english} Easy Run` })])

    expect(screen.getByText('Easy Run')).toBeInTheDocument()
    // The screen reader still hears the whole title.
    expect(screen.getByRole('button', { name: new RegExp(`${english} Easy Run`) })).toBeInTheDocument()
  })

  it('marks an untranslated title as English, so it is hyphenated as English (ai-trainer-ops#324)', () => {
    useAppStore.setState({ uiLanguage: 'de', planTranslations: { 'Endurance Ride': 'Grundlagenfahrt' } })
    try {
      renderStrip([plan(dayOffset(-1), { title: 'Endurance Ride' }), plan(dayOffset(0), { title: 'Recovery Spin' })])

      expect(screen.getByText('Recovery Spin')).toHaveAttribute('lang', 'en')
      expect(screen.getByText('Grundlagenfahrt')).not.toHaveAttribute('lang')
    } finally {
      useAppStore.setState({ uiLanguage: 'auto', planTranslations: {} })
    }
  })

  it('tells a rest day on a training day from one on a free day (ai-trainer-ops#324)', () => {
    const onTrainingDay = dayOffset(1)
    const offDay = dayOffset(2)
    const key = (date: string) =>
      new Date(`${date}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long' }).toLowerCase()
    setProfile({ trainingDays: [key(onTrainingDay)] } as UserProfile)
    try {
      renderStrip([
        plan(onTrainingDay, { workoutType: 'rest', title: 'Rest Day', durationMinutes: 0 }),
        plan(offDay, { workoutType: 'rest', title: 'Rest Day', durationMinutes: 0 }),
      ])

      const kept = screen.getByRole('button', { name: /a training day, kept free$/ })
      expect(kept).toHaveClass('border-dashed')
      expect(kept).toHaveTextContent('kept free')
      expect(screen.getAllByText('kept free')).toHaveLength(1)
    } finally {
      setProfile(null)
    }
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
      screen.getByRole('button', { name: `${weekday} — Intervals, Session, 1h; Recovery, Session, 1h` })
    ).toBeInTheDocument()
    expect(screen.getByText('2 sessions')).toBeInTheDocument()
    // Two durations do not fit a chip, so it shows the day's total.
    expect(screen.getByText('2h')).toBeInTheDocument()
  })

  it('sums both ends of a two-a-day\'s windows', () => {
    const date = dayOffset(2)
    renderStrip([
      plan(date, { slot: 0, durationMinutes: 75, durationMinMinutes: 60, durationMaxMinutes: 90 }),
      plan(date, { slot: 1, durationMinutes: 150, durationMinMinutes: 120, durationMaxMinutes: 180 }),
    ])

    expect(screen.getByText('3–4.5h')).toBeInTheDocument()
  })

  it('claims no total when a session of the day has no duration', () => {
    const date = dayOffset(2)
    renderStrip([plan(date, { slot: 0 }), plan(date, { slot: 1, durationMinutes: 0 })])

    expect(screen.getByText('2 sessions')).toBeInTheDocument()
    expect(screen.queryByText(/^\d+(\.\d+)?h$|min$/)).not.toBeInTheDocument()
  })

  it('only ticks a two-a-day off once both halves are done', () => {
    const date = dayOffset(-3)
    renderStrip([
      plan(date, { slot: 0, completed: true }),
      plan(date, { slot: 1, completed: false }),
    ])

    expect(document.querySelectorAll('svg.text-emerald-500')).toHaveLength(0)
  })

  // ai-trainer-ops#90: a chip said only "Recovery" on a desktop and "Easy" on a
  // phone, so every day had to be opened to see what it was.
  it('shows the day\'s title and duration, in the same words at every width', () => {
    renderStrip([plan(dayOffset(1), { workoutType: 'recovery', title: 'Easy spin', durationMinutes: 45 })])

    expect(screen.getByText('Easy spin')).toBeInTheDocument()
    expect(screen.getByText('45 min')).toBeInTheDocument()
    // One text per chip, not a phone and a desktop variant of it.
    expect(document.querySelector('.sm\\:hidden, .hidden')).toBeNull()
  })

  it('falls back to the type when a session has no title', () => {
    renderStrip([plan(dayOffset(1), { workoutType: 'recovery', title: '' })])

    expect(screen.getByText('Recovery')).toBeInTheDocument()
  })

  it('offers the month behind "Calendar"', async () => {
    const onShowMore = vi.fn()
    renderStrip([plan(dayOffset(0))], { onShowMore })

    await userEvent.click(screen.getByRole('button', { name: 'Calendar' }))

    expect(onShowMore).toHaveBeenCalledTimes(1)
  })

  it('leaves the header bare where there is nothing more to show', () => {
    renderStrip([plan(dayOffset(0))])

    expect(screen.queryByRole('button', { name: 'Calendar' })).not.toBeInTheDocument()
  })
})
