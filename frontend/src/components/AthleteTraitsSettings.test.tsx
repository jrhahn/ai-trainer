import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AthleteTraitsSettings from './AthleteTraitsSettings'
import type { AthleteMemoryFact } from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())
const mockUpdate = vi.hoisted(() => vi.fn())
const mockConfirm = vi.hoisted(() => vi.fn())
const mockDelete = vi.hoisted(() => vi.fn())

vi.mock('../services/user', () => ({
  fetchAthleteMemoryFacts: mockFetch,
  updateAthleteMemoryFact: mockUpdate,
  confirmAthleteMemoryFact: mockConfirm,
  deleteAthleteMemoryFact: mockDelete,
}))

// Apply the selector so `useAppStore((s) => s.authToken)` returns the token.
vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function makeFact(overrides: Partial<AthleteMemoryFact> = {}): AthleteMemoryFact {
  return {
    id: 'fact-1',
    fact: 'Adds extra work after rest days',
    category: 'coaching_risk',
    sourceSnippet: '',
    sourceExchangeId: null,
    firstObservedAt: '2026-06-01T00:00:00Z',
    lastConfirmedAt: '2026-06-10T00:00:00Z',
    confidence: 0.6,
    status: 'active',
    observationCount: 2,
    updatedAt: '2026-06-10T00:00:00Z',
    ...overrides,
  }
}

function renderComponent() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <AthleteTraitsSettings />
    </QueryClientProvider>,
  )
}

describe('AthleteTraitsSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('groups learned traits by category', async () => {
    mockFetch.mockResolvedValue([
      makeFact({ id: 'a', category: 'coaching_risk', fact: 'Overreaches when fresh' }),
      makeFact({ id: 'b', category: 'preference', fact: 'Prefers long endurance rides' }),
    ])
    renderComponent()

    expect(await screen.findByText('Overreaches when fresh')).toBeInTheDocument()
    expect(screen.getByText('Prefers long endurance rides')).toBeInTheDocument()
    // Slugs are rendered as readable category headings.
    expect(screen.getByText('Coaching risk')).toBeInTheDocument()
    expect(screen.getByText('Preference')).toBeInTheDocument()
  })

  it('shows an empty state when there are no traits', async () => {
    mockFetch.mockResolvedValue([])
    renderComponent()
    expect(await screen.findByText(/No learned traits yet/i)).toBeInTheDocument()
  })

  it('edits a trait and persists the correction', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([makeFact()])
    mockUpdate.mockResolvedValue(makeFact({ fact: 'Adds easy spins after rest days' }))
    renderComponent()

    await screen.findByText('Adds extra work after rest days')
    await user.click(screen.getByRole('button', { name: /edit trait/i }))

    const textarea = screen.getByRole('textbox', { name: /edit trait/i })
    await user.clear(textarea)
    await user.type(textarea, 'Adds easy spins after rest days')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('test-token', 'fact-1', {
        fact: 'Adds easy spins after rest days',
      }),
    )
  })

  it('confirms a trait', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([makeFact()])
    mockConfirm.mockResolvedValue(makeFact({ status: 'user_confirmed' }))
    renderComponent()

    await screen.findByText('Adds extra work after rest days')
    await user.click(screen.getByRole('button', { name: /confirm trait/i }))

    await waitFor(() =>
      expect(mockConfirm).toHaveBeenCalledWith('test-token', 'fact-1'),
    )
  })

  it('deletes a trait after confirmation', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch.mockResolvedValue([makeFact()])
    mockDelete.mockResolvedValue(undefined)
    renderComponent()

    const row = (await screen.findByText('Adds extra work after rest days')).closest('li')!
    await user.click(within(row).getByRole('button', { name: /delete trait/i }))

    await waitFor(() =>
      expect(mockDelete).toHaveBeenCalledWith('test-token', 'fact-1'),
    )
    confirmSpy.mockRestore()
  })

  it('does not delete when the user cancels the confirm dialog', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    mockFetch.mockResolvedValue([makeFact()])
    renderComponent()

    const row = (await screen.findByText('Adds extra work after rest days')).closest('li')!
    await user.click(within(row).getByRole('button', { name: /delete trait/i }))

    expect(mockDelete).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('hides the confirm action for already-confirmed traits', async () => {
    mockFetch.mockResolvedValue([makeFact({ status: 'user_confirmed' })])
    renderComponent()

    await screen.findByText('Adds extra work after rest days')
    expect(screen.queryByRole('button', { name: /confirm trait/i })).toBeNull()
    expect(screen.getByText(/Confirmed/i)).toBeInTheDocument()
  })
})
