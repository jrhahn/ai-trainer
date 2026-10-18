import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SeasonPlan from './SeasonPlan'
import { useAppStore } from '../store/useAppStore'
import type { SeasonWeek } from '../services/ai'

const mockFetchSeasonPlan = vi.hoisted(() => vi.fn())
const mockWriteWeekGoals = vi.hoisted(() => vi.fn())

vi.mock('../services/ai', () => ({
  fetchSeasonPlan: mockFetchSeasonPlan,
  writeWeekGoals: mockWriteWeekGoals,
}))

function week(overrides: Partial<SeasonWeek> = {}): SeasonWeek {
  return {
    weekStart: '2026-10-12',
    phase: 'build',
    focus: 'threshold and sweet spot',
    recovery: false,
    loadWeek: 2,
    hoursMin: 6.5,
    hoursMax: 8,
    hardSessionsMin: 2,
    hardSessionsMax: 2,
    keySession: 'two quality sessions and one long ride',
    nextRace: '2026-12-27',
    goal: 'Raise the ceiling you will race under.',
    ...overrides,
  }
}

const RECOVERY = week({
  weekStart: '2026-10-19',
  recovery: true,
  loadWeek: null,
  hoursMin: 4,
  hoursMax: 5,
  hardSessionsMin: 0,
  hardSessionsMax: 1,
  goal: 'Let the last three weeks settle.',
})

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'token' })
})

describe('SeasonPlan', () => {
  it('states this week and keeps the weeks ahead one tap away', async () => {
    mockFetchSeasonPlan.mockResolvedValue([week(), RECOVERY])
    render(<SeasonPlan />)

    expect(
      await screen.findByRole('heading', { name: 'This week: Build · threshold and sweet spot' })
    ).toBeInTheDocument()
    expect(screen.getByText('Load week 2 of 3 · 6.5–8 h · 2 hard sessions')).toBeInTheDocument()
    expect(screen.getByText('Raise the ceiling you will race under.')).toBeInTheDocument()

    await userEvent.click(screen.getByText('The next week'))
    expect(screen.getByText('Recovery week · 4–5 h · at most 1 hard session')).toBeVisible()
    expect(screen.getByText('Let the last three weeks settle.')).toBeVisible()
    expect(mockWriteWeekGoals).not.toHaveBeenCalled()
  })

  it('words a single hour target and a range of hard sessions', async () => {
    mockFetchSeasonPlan.mockResolvedValue([
      week({ phase: 'taper', loadWeek: null, hoursMin: 3, hoursMax: 3, hardSessionsMin: 1, hardSessionsMax: 2 }),
      week({ weekStart: '2026-10-19', phase: 'transition', loadWeek: null, hoursMin: null, hoursMax: null, hardSessionsMin: 0, hardSessionsMax: 0 }),
      RECOVERY,
    ])
    render(<SeasonPlan />)

    expect(await screen.findByText('3 h · 1–2 hard sessions')).toBeInTheDocument()
    await userEvent.click(screen.getByText('The next 2 weeks'))
    expect(screen.getByText('no hard sessions')).toBeVisible()
  })

  it('asks for the goals the weeks are missing, once', async () => {
    mockFetchSeasonPlan.mockResolvedValue([week({ goal: null }), RECOVERY])
    mockWriteWeekGoals.mockResolvedValue([week({ goal: 'Written now.' }), RECOVERY])
    render(<SeasonPlan />)

    expect(await screen.findByText('Written now.')).toBeInTheDocument()
    expect(mockWriteWeekGoals).toHaveBeenCalledTimes(1)
  })

  it('shows the targets when the goals cannot be written', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetchSeasonPlan.mockResolvedValue([week({ goal: null })])
    mockWriteWeekGoals.mockRejectedValue(new Error('no key'))
    render(<SeasonPlan />)

    expect(await screen.findByText('Load week 2 of 3 · 6.5–8 h · 2 hard sessions')).toBeInTheDocument()
    await waitFor(() => expect(mockWriteWeekGoals).toHaveBeenCalled())
    expect(await screen.findByText('The week goals follow on your next visit.')).toBeInTheDocument()
    expect(screen.queryByText('Writing the week goals…')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    error.mockRestore()
  })

  it('says the weeks could not be read, and reads them again on retry (ai-trainer-ops#135)', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetchSeasonPlan.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce([week()])
    render(<SeasonPlan />)

    expect(await screen.findByText('The weeks ahead could not be loaded.')).toBeInTheDocument()
    expect(error).toHaveBeenCalledWith('[SeasonPlan] Failed to load the season plan:', expect.any(Error))

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(
      await screen.findByRole('heading', { name: 'This week: Build · threshold and sweet spot' })
    ).toBeInTheDocument()
    expect(screen.queryByText('The weeks ahead could not be loaded.')).not.toBeInTheDocument()
    error.mockRestore()
  })

  it('renders nothing while the weeks are first read', () => {
    mockFetchSeasonPlan.mockReturnValue(new Promise(() => {}))
    const { container } = render(<SeasonPlan />)

    expect(container).toBeEmptyDOMElement()
  })

  it('says the goals are being written while they are', async () => {
    let finish: (weeks: SeasonWeek[]) => void = () => {}
    mockFetchSeasonPlan.mockResolvedValue([week({ goal: null })])
    mockWriteWeekGoals.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    render(<SeasonPlan />)

    expect(await screen.findByText('Writing the week goals…')).toBeInTheDocument()

    finish([week({ goal: 'Written now.' })])

    expect(await screen.findByText('Written now.')).toBeInTheDocument()
    expect(screen.queryByText('Writing the week goals…')).not.toBeInTheDocument()
  })

  it('reads the weeks again when a race is added', async () => {
    mockFetchSeasonPlan.mockResolvedValue([week()])
    render(<SeasonPlan />)
    await screen.findByText('Raise the ceiling you will race under.')

    useAppStore.getState().addRaceEvent({ id: 'r1', date: '2026-12-27', distanceKm: 120, elevationM: 1500 } as never)

    await waitFor(() => expect(mockFetchSeasonPlan).toHaveBeenCalledTimes(2))
  })
})

describe('SeasonPlan in German (ai-trainer-ops#104)', () => {
  it('dates the weeks ahead in the interface language, not the browser default', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    try {
      mockFetchSeasonPlan.mockResolvedValue([week(), RECOVERY])
      render(<SeasonPlan />)

      await userEvent.click(await screen.findByText('Die nächste Woche'))
      // jsdom's browser speaks en-US, so this only reads German through the interface language.
      expect(screen.getByText('19. Okt.')).toBeInTheDocument()
    } finally {
      useAppStore.setState({ uiLanguage: 'auto' })
    }
  })
})
