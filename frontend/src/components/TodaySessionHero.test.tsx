import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('opens the instructions over the dashboard instead of navigating away', async () => {
    renderHero({ day: day({ keyFocusPoints: ['Keep cadence smooth and high'] }) })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /show details/i }))

    const dialog = screen.getByRole('dialog', { name: '4 x 8 min threshold' })
    expect(within(dialog).getByText('Keep cadence smooth and high')).toBeInTheDocument()
  })

  it('still reaches the full page, where logging and history live', async () => {
    renderHero({ day: day() })

    await userEvent.click(screen.getByRole('button', { name: /show details/i }))

    expect(screen.getByRole('link', { name: /open the full session page/i })).toHaveAttribute(
      'href',
      '/workout/2026-08-26'
    )
  })

  it('counts a ride logged today as done even when the plan day was never ticked', async () => {
    renderHero({ day: day({ completed: false }), loggedToday: true })

    expect(screen.getByText('Done')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /show details/i }))
    expect(screen.getByRole('link', { name: /review and log this session/i })).toBeInTheDocument()
  })

  it('drops the duration on a rest day rather than showing 0m', () => {
    renderHero({ day: day({ workoutType: 'rest', title: 'Rest day', durationMinutes: 0 }) })

    expect(screen.getByRole('heading', { name: 'Rest day' })).toBeInTheDocument()
    expect(screen.queryByText('0m')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /show details/i })).toBeInTheDocument()
  })

  it('says so when the plan has nothing for today', () => {
    renderHero({ day: undefined })

    expect(screen.getByText(/no session planned for today/i)).toBeInTheDocument()
  })
})
