import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import PlanChangesPanel from './PlanChangesPanel'
import type { PlanDayHistoryEntry, PlanDayHistoryStats } from '../services/user'

const { mockFetchStats, mockFetchHistory } = vi.hoisted(() => ({
  mockFetchStats: vi.fn(),
  mockFetchHistory: vi.fn(),
}))
vi.mock('../services/user', () => ({
  fetchPlanHistoryStats: mockFetchStats,
  fetchPlanHistory: mockFetchHistory,
}))

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <PlanChangesPanel authToken="tok" />
    </QueryClientProvider>,
  )
}

const stats: PlanDayHistoryStats = {
  bySource: { coach_chat: 2, auto_adapt: 1 },
  appliedCount: 2,
  blockedCount: 1,
  mostChangedDates: [{ date: '2026-05-01', count: 2 }],
  total: 3,
}

const entries: PlanDayHistoryEntry[] = [
  {
    id: 'h1',
    date: '2026-05-01',
    source: 'coach_chat',
    applied: true,
    recordedAt: '2026-05-01T10:00:00Z',
    oldDay: null,
    newDay: { workoutType: 'intervals', title: 'VO2max' },
  },
  {
    id: 'h2',
    date: '2026-05-02',
    source: 'auto_adapt',
    applied: false,
    recordedAt: '2026-05-02T10:00:00Z',
    oldDay: { workoutType: 'intervals' },
    newDay: { workoutType: 'recovery' },
  },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('PlanChangesPanel', () => {
  it('renders nothing when there is no history', async () => {
    mockFetchStats.mockResolvedValue({ ...stats, total: 0 })
    mockFetchHistory.mockResolvedValue([])
    const { container } = renderPanel()
    await waitFor(() => expect(mockFetchStats).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows analytics and a recent timeline including a blocked entry', async () => {
    mockFetchStats.mockResolvedValue(stats)
    mockFetchHistory.mockResolvedValue(entries)
    renderPanel()

    expect(await screen.findByText('Recent plan changes')).toBeInTheDocument()
    // applied vs blocked counts surfaced
    expect(screen.getByText('Kept yours')).toBeInTheDocument()
    expect(screen.getByText('Coach chat: Added intervals — VO2max')).toBeInTheDocument()
    expect(
      screen.getByText('Auto-adaptation wanted to type intervals → recovery but kept your version'),
    ).toBeInTheDocument()
  })

  it('renders every fetched entry (no 6-item cap) so the scrollable list is complete', async () => {
    const many: PlanDayHistoryEntry[] = Array.from({ length: 12 }, (_, i) => ({
      id: `h${i}`,
      date: `2026-05-${String(i + 1).padStart(2, '0')}`,
      source: 'user_edit',
      applied: true,
      recordedAt: `2026-05-${String(i + 1).padStart(2, '0')}T10:00:00Z`,
      oldDay: { title: `Old ${i}` },
      newDay: { title: `New ${i}` },
    }))
    mockFetchStats.mockResolvedValue({ ...stats, total: 12 })
    mockFetchHistory.mockResolvedValue(many)
    renderPanel()

    // The 12th entry (well past the old 6-item cap) must still be rendered.
    expect(
      await screen.findByText('Your edit: title “Old 11” → “New 11”'),
    ).toBeInTheDocument()
  })
})
