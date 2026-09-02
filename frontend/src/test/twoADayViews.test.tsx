/**
 * The bug class, guarded across the views that turn a plan into "the day" (#648).
 *
 * "Take the plan day for this date" is the obvious thing to write and it is
 * wrong whenever the date holds two sessions. It has been fixed twice and come
 * back in three shapes — `plan.find((d) => d.date === iso)` in the hero (#496,
 * then #645), a `Map` keyed by date in the week strip (#645), and the same
 * `.find()` in `planForRide`.
 *
 * `utils/planSessions.ts` was correct throughout, and its unit tests could not
 * see any of it: every recurrence came from a redesign that left the data layer
 * alone and rewrote a view. So this asks the *views* the question instead —
 * given a date with two sessions, does the second one survive?
 *
 * Component-level cases live next to their components (SessionHero, WeekStrip,
 * DashboardPage). This file holds the ones with no natural home.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { planForRide } from '../pages/DashboardPage'
import TrainingCalendar from '../components/TrainingCalendar'
import { useAppStore } from '../store/useAppStore'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => vi.fn() }
})

const DATE = '2026-09-15'

const session = (overrides: Partial<TrainingDay> & { slot?: number }): TrainingDay => ({
  date: DATE,
  workoutType: 'endurance',
  title: 'Session',
  description: '',
  durationMinutes: 60,
  ...overrides,
})

const ride = (overrides: Partial<RideMetricPoint> = {}): RideMetricPoint =>
  ({ activityDate: DATE, sportType: 'Ride', ...overrides }) as RideMetricPoint

const AM = session({ slot: 0, title: 'Morning intervals', workoutType: 'intervals' })
const PM = session({ slot: 1, title: 'Evening spin', workoutType: 'recovery' })

describe('planForRide', () => {
  it('picks the session the ride was matched to, not the first of the day', () => {
    const matched = ride({
      matchedPlanDate: DATE,
      matchedPlanSnapshot: { date: DATE, slot: 1 },
    })

    expect(planForRide(matched, [AM, PM])?.title).toBe('Evening spin')
  })

  it('still returns the only session of a normal day', () => {
    expect(planForRide(ride(), [AM])?.title).toBe('Morning intervals')
  })

  it('treats a matched snapshot without a slot as the first session', () => {
    // Legacy single-session days carry no slot and read back as slot 0.
    const matched = ride({ matchedPlanDate: DATE, matchedPlanSnapshot: { date: DATE } })

    expect(planForRide(matched, [AM, PM])?.title).toBe('Morning intervals')
  })

  it('declines to guess for an unmatched ride on a two-a-day', () => {
    // "planned: Morning intervals" against what may have been the evening ride
    // is worse than showing no comparison at all.
    expect(planForRide(ride(), [AM, PM])).toBeNull()
  })

  it('falls back to the snapshot when the plan day is outside the loaded window', () => {
    const matched = ride({
      matchedPlanDate: DATE,
      matchedPlanSnapshot: { date: DATE, slot: 1, title: 'Evening spin' },
    })

    expect(planForRide(matched, [])?.title).toBe('Evening spin')
  })

  it('ignores a snapshot pointing at a different date', () => {
    const stale = ride({
      matchedPlanDate: '2026-09-01',
      matchedPlanSnapshot: { date: '2026-09-01', title: 'Some other day' },
    })

    expect(planForRide(stale, [])).toBeNull()
  })
})

describe('TrainingCalendar', () => {
  it('shows both sessions of a two-a-day', () => {
    useAppStore.setState({ trainingPlan: [AM, PM], rideMetricsHistory: [] })

    render(
      <MemoryRouter>
        <TrainingCalendar />
      </MemoryRouter>
    )

    // The calendar has rendered these correctly all along; this locks it in, so
    // the next redesign of it fails here rather than in production.
    expect(screen.getAllByText(/Morning intervals/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Evening spin/).length).toBeGreaterThan(0)
  })
})
