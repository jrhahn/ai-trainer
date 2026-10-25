import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RaceEventEditor from './RaceEventEditor'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent, TrainingDay } from '../store/useAppStore'
import { setPlan } from '../queries/plan'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }))
vi.mock('../services/user', () => ({
  createRaceEvent: vi.fn(),
  deleteRaceEventRemote: vi.fn(),
  updateRaceEventRemote: vi.fn(),
}))

const DATE = '2026-10-06'

function open(plan: TrainingDay[], raceEvents: RaceEvent[] = []) {
  setPlan(plan)
  useAppStore.setState({ authToken: 'tok', raceEvents })
  render(<RaceEventEditor date={DATE} onClose={vi.fn()} onSaved={vi.fn()} />)
}

describe('RaceEventEditor (ai-trainer-ops#274)', () => {
  afterEach(() => {
    useAppStore.getState().resetAll()
    vi.clearAllMocks()
  })

  it('links each session of a two-a-day, the evening one by its slot (#496)', async () => {
    open([
      { date: DATE, slot: 0, timeOfDay: 'am', workoutType: 'endurance', title: 'Spin' },
      { date: DATE, slot: 1, timeOfDay: 'pm', workoutType: 'strength', title: 'Gym' },
    ] as TrainingDay[])

    await userEvent.click(screen.getByRole('button', { name: 'Open AM planned workout' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open PM planned workout' }))

    expect(mockNavigate.mock.calls).toEqual([[`/workout/${DATE}`], [`/workout/${DATE}?slot=1`]])
  })

  it('links a single session without a label, and says when a race starts', () => {
    open(
      [{ date: DATE, workoutType: 'endurance', title: 'Spin' }] as TrainingDay[],
      [{ id: 'r1', date: DATE, startTime: '09:30', distanceKm: 100, elevationM: 1200 }] as RaceEvent[]
    )

    expect(screen.getByRole('button', { name: 'Open planned workout' })).toBeInTheDocument()
    expect(screen.getByText(`${DATE} at 09:30`)).toBeInTheDocument()
  })
})
