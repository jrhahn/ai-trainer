import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DayBoardWhy from './DayBoardWhy'
import { useAppStore } from '../store/useAppStore'

const mocks = vi.hoisted(() => ({ fetchDayBoard: vi.fn() }))
vi.mock('../services/user', () => mocks)

const source = { path: 'backend/services/freshness_allocation.py', line: 341, symbol: 'allocate_day', ref: '0a1b2c3' }

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'test-token' })
})

describe('DayBoardWhy', () => {
  it('loads only when opened, then shows each deduction and links the code', async () => {
    mocks.fetchDayBoard.mockResolvedValue({
      hot: true,
      heatTolerance: '',
      options: [
        {
          key: 'trail', label: 'Trail day', prescription: '', modality: 'mtb',
          base: 6.1, heatCost: 2.7, freshnessCost: 0, competesWith: '', competesWithLabel: '', score: 3.4,
        },
        {
          key: 'key_session', label: 'Key session', prescription: '', modality: 'road',
          base: 5.5, heatCost: 0, freshnessCost: 1.2, competesWith: 'trail',
          competesWithLabel: 'the weekend trail ride', score: 4.3,
        },
      ],
      source,
    })
    render(<DayBoardWhy />)
    expect(mocks.fetchDayBoard).not.toHaveBeenCalled()

    await userEvent.click(screen.getByText('What the planner weighs for a day'))

    expect(await screen.findByText('Key session')).toBeInTheDocument()
    expect(screen.getByText(/−1.2 freshness for the weekend trail ride/)).toBeInTheDocument()
    expect(screen.getByText(/−2.7 heat/)).toBeInTheDocument()
    expect(screen.getByText(/heat tolerance is not measured yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /allocate_day/ })).toHaveAttribute(
      'href',
      'https://github.com/jrhahn/ai-trainer/blob/0a1b2c3/backend/services/freshness_allocation.py#L341',
    )
  })

  it('says so when nothing is known yet', async () => {
    mocks.fetchDayBoard.mockResolvedValue({ hot: false, heatTolerance: '', options: [], source })
    render(<DayBoardWhy />)
    await userEvent.click(screen.getByText('What the planner weighs for a day'))
    expect(await screen.findByText(/Nothing to weigh yet/)).toBeInTheDocument()
  })

  it('says so when it cannot load, and clears that once a retry works', async () => {
    mocks.fetchDayBoard.mockRejectedValueOnce(new Error('offline'))
    mocks.fetchDayBoard.mockResolvedValueOnce({ hot: false, heatTolerance: '', options: [], source })
    render(<DayBoardWhy />)
    const toggle = screen.getByText('What the planner weighs for a day')
    await userEvent.click(toggle)
    expect(await screen.findByText('Could not load it. Try again later.')).toBeInTheDocument()

    await userEvent.click(toggle)
    await userEvent.click(toggle)

    expect(await screen.findByText(/Nothing to weigh yet/)).toBeInTheDocument()
    expect(screen.queryByText('Could not load it. Try again later.')).not.toBeInTheDocument()
  })

  it('sends one request while one is in flight', async () => {
    let resolve: (value: unknown) => void = () => {}
    mocks.fetchDayBoard.mockReturnValue(new Promise((r) => (resolve = r)))
    render(<DayBoardWhy />)
    const toggle = screen.getByText('What the planner weighs for a day')
    await userEvent.click(toggle)
    await userEvent.click(toggle)
    await userEvent.click(toggle)
    resolve({ hot: false, heatTolerance: '', options: [], source })
    expect(await screen.findByText(/Nothing to weigh yet/)).toBeInTheDocument()
    expect(mocks.fetchDayBoard).toHaveBeenCalledTimes(1)
  })
})
