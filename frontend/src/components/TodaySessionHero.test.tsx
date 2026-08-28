import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TodaySessionHero from './TodaySessionHero'
import type { TrainingDay } from '../store/useAppStore'
import { useAppStore } from '../store/useAppStore'

const day = (overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date: '2026-08-26',
  workoutType: 'intervals',
  title: '4 x 8 min threshold',
  description: '4 x 8 min at 265-280 W, 4 min easy between.',
  durationMinutes: 75,
  ...overrides,
})

function renderHero(props: Parameters<typeof TodaySessionHero>[0]) {
  render(
    <MemoryRouter>
      <TodaySessionHero {...props} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('TodaySessionHero', () => {
  it('names the session and what it asks for', () => {
    renderHero({ day: day({ targetPower: { low: 265, high: 280 } }) })

    expect(screen.getByRole('heading', { name: '4 x 8 min threshold' })).toBeInTheDocument()
    expect(screen.getByText(/4 x 8 min at 265-280 W/)).toBeInTheDocument()
    expect(screen.getByText('265–280 W')).toBeInTheDocument()
    expect(screen.getByText('1h 15m')).toBeInTheDocument()
  })

  it('links to the session', () => {
    renderHero({ day: day() })

    expect(screen.getByRole('link', { name: /open the session/i })).toHaveAttribute(
      'href',
      '/workout/2026-08-26'
    )
  })

  it('counts a ride logged today as done even when the plan day was never ticked', () => {
    renderHero({ day: day({ completed: false }), loggedToday: true })

    expect(screen.getByText('Done')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /review the session/i })).toBeInTheDocument()
  })

  it('drops the duration on a rest day rather than showing 0m', () => {
    renderHero({ day: day({ workoutType: 'rest', title: 'Rest day', durationMinutes: 0 }) })

    expect(screen.getByRole('heading', { name: 'Rest day' })).toBeInTheDocument()
    expect(screen.queryByText('0m')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /see the day/i })).toBeInTheDocument()
  })

  it('says so when the plan has nothing for today', () => {
    renderHero({ day: undefined })

    expect(screen.getByText(/no session planned for today/i)).toBeInTheDocument()
  })
})
