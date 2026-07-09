import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AthleteTraitsSettings from './AthleteTraitsSettings'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteMemoryFact,
} from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())
const mockUpdate = vi.hoisted(() => vi.fn())
const mockConfirm = vi.hoisted(() => vi.fn())
const mockDelete = vi.hoisted(() => vi.fn())
const mockFetchPrivacy = vi.hoisted(() => vi.fn())
const mockUpdatePrivacy = vi.hoisted(() => vi.fn())
const mockClearAll = vi.hoisted(() => vi.fn())
const mockExport = vi.hoisted(() => vi.fn())
const mockFetchHypotheses = vi.hoisted(() => vi.fn())
const mockConfirmHypothesis = vi.hoisted(() => vi.fn())
const mockRefuteHypothesis = vi.hoisted(() => vi.fn())
const mockDeleteHypothesis = vi.hoisted(() => vi.fn())
const mockFetchExperiments = vi.hoisted(() => vi.fn())
const mockCompleteExperiment = vi.hoisted(() => vi.fn())
const mockDismissExperiment = vi.hoisted(() => vi.fn())
const mockDeleteExperiment = vi.hoisted(() => vi.fn())

vi.mock('../services/user', () => ({
  fetchAthleteMemoryFacts: mockFetch,
  updateAthleteMemoryFact: mockUpdate,
  confirmAthleteMemoryFact: mockConfirm,
  deleteAthleteMemoryFact: mockDelete,
  fetchMemoryPrivacySettings: mockFetchPrivacy,
  updateMemoryPrivacySettings: mockUpdatePrivacy,
  clearAllMemory: mockClearAll,
  exportMemory: mockExport,
  fetchAthleteHypotheses: mockFetchHypotheses,
  confirmAthleteHypothesis: mockConfirmHypothesis,
  refuteAthleteHypothesis: mockRefuteHypothesis,
  deleteAthleteHypothesis: mockDeleteHypothesis,
  fetchValidationExperiments: mockFetchExperiments,
  completeValidationExperiment: mockCompleteExperiment,
  dismissValidationExperiment: mockDismissExperiment,
  deleteValidationExperiment: mockDeleteExperiment,
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
    contradictionNote: null,
    observationCount: 2,
    updatedAt: '2026-06-10T00:00:00Z',
    ...overrides,
  }
}

function makeHypothesis(
  overrides: Partial<AthleteHypothesis> = {},
): AthleteHypothesis {
  return {
    id: 'hyp-1',
    statement: 'Upper-body strength suppresses next-day HR response',
    category: 'fatigue_response',
    rationale: 'HR ~8 bpm low the day after gym sessions.',
    confidence: 0.38,
    evidenceCount: 2,
    status: 'proposed',
    firstProposedAt: '2026-06-01T00:00:00Z',
    updatedAt: '2026-06-10T00:00:00Z',
    ...overrides,
  }
}

function makeExperiment(
  overrides: Partial<AthleteExperiment> = {},
): AthleteExperiment {
  return {
    id: 'exp-1',
    hypothesisId: 'hyp-1',
    question: 'Does upper-body strength suppress next-day HR?',
    protocol: 'Repeat the gym session and compare HR on the next easy ride.',
    rationale: 'A clear HR drop would confirm it.',
    category: 'fatigue_response',
    status: 'suggested',
    createdAt: '2026-06-01T00:00:00Z',
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
    mockFetchPrivacy.mockResolvedValue({ memoryUpdatesEnabled: true })
    mockUpdatePrivacy.mockResolvedValue({ memoryUpdatesEnabled: false })
    mockClearAll.mockResolvedValue(undefined)
    mockExport.mockResolvedValue({ facts: [] })
    mockFetchHypotheses.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([])
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

  it('flags a contradicted trait for validation and shows the reason', async () => {
    mockFetch.mockResolvedValue([
      makeFact({
        status: 'needs_validation',
        contradictionNote: 'Held 400 W for 5x4 min — well above the stored 320 W FTP.',
      }),
    ])
    renderComponent()

    await screen.findByText('Adds extra work after rest days')
    expect(screen.getByText(/Needs validation/i)).toBeInTheDocument()
    expect(
      screen.getByText(/Held 400 W for 5x4 min/),
    ).toBeInTheDocument()
    // The athlete can still confirm it to resolve the flag.
    expect(screen.getByRole('button', { name: /confirm trait/i })).toBeInTheDocument()
  })

  it('renders a working hypothesis with its confidence and evidence', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
    renderComponent()

    expect(
      await screen.findByText('Upper-body strength suppresses next-day HR response'),
    ).toBeInTheDocument()
    expect(screen.getByText('Working Hypotheses')).toBeInTheDocument()
    expect(screen.getByText(/Needs validation/i)).toBeInTheDocument()
    expect(screen.getByText(/confidence 38%/)).toBeInTheDocument()
    expect(screen.getByText(/2 observations/)).toBeInTheDocument()
  })

  it('confirms a hypothesis', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
    mockConfirmHypothesis.mockResolvedValue(makeHypothesis({ status: 'confirmed' }))
    renderComponent()

    await screen.findByText('Upper-body strength suppresses next-day HR response')
    await user.click(screen.getByRole('button', { name: /confirm hypothesis/i }))

    await waitFor(() =>
      expect(mockConfirmHypothesis).toHaveBeenCalledWith('test-token', 'hyp-1'),
    )
  })

  it('dismisses a hypothesis', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
    mockRefuteHypothesis.mockResolvedValue(makeHypothesis({ status: 'refuted' }))
    renderComponent()

    await screen.findByText('Upper-body strength suppresses next-day HR response')
    await user.click(screen.getByRole('button', { name: /dismiss hypothesis/i }))

    await waitFor(() =>
      expect(mockRefuteHypothesis).toHaveBeenCalledWith('test-token', 'hyp-1'),
    )
  })

  it('does not show the hypotheses section when there are none', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchHypotheses.mockResolvedValue([])
    renderComponent()

    await screen.findByText(/No learned traits yet/i)
    expect(screen.queryByText('Working Hypotheses')).toBeNull()
  })

  it('renders a suggested experiment with its question', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    renderComponent()

    expect(
      await screen.findByText(
        'Repeat the gym session and compare HR on the next easy ride.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Suggested Experiments')).toBeInTheDocument()
    expect(
      screen.getByText('Does upper-body strength suppress next-day HR?'),
    ).toBeInTheDocument()
  })

  it('completes an experiment', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockCompleteExperiment.mockResolvedValue(
      makeExperiment({ status: 'completed' }),
    )
    renderComponent()

    await screen.findByText(
      'Repeat the gym session and compare HR on the next easy ride.',
    )
    await user.click(screen.getByRole('button', { name: /complete experiment/i }))

    await waitFor(() =>
      expect(mockCompleteExperiment).toHaveBeenCalledWith('test-token', 'exp-1'),
    )
  })

  it('dismisses an experiment', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockDismissExperiment.mockResolvedValue(
      makeExperiment({ status: 'dismissed' }),
    )
    renderComponent()

    await screen.findByText(
      'Repeat the gym session and compare HR on the next easy ride.',
    )
    await user.click(screen.getByRole('button', { name: /dismiss experiment/i }))

    await waitFor(() =>
      expect(mockDismissExperiment).toHaveBeenCalledWith('test-token', 'exp-1'),
    )
  })

  it('does not show the experiments section when there are none', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([])
    renderComponent()

    await screen.findByText(/No learned traits yet/i)
    expect(screen.queryByText('Suggested Experiments')).toBeNull()
  })

  it('toggles the "learn from conversations" privacy switch', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    renderComponent()

    const toggle = await screen.findByRole('switch')
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    await user.click(toggle)

    await waitFor(() =>
      expect(mockUpdatePrivacy).toHaveBeenCalledWith('test-token', { memoryUpdatesEnabled: false })
    )
  })

  it('shows the disabled banner when memory updates are off', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchPrivacy.mockResolvedValue({ memoryUpdatesEnabled: false })
    renderComponent()

    expect(await screen.findByText(/Memory updates are disabled/)).toBeInTheDocument()
  })

  it('clears all memory after the user confirms', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch.mockResolvedValue([])
    renderComponent()

    await user.click(await screen.findByRole('button', { name: /Clear all memory/ }))

    await waitFor(() => expect(mockClearAll).toHaveBeenCalledWith('test-token'))
    expect(await screen.findByText('All coaching memory cleared.')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('does not clear memory when the user cancels', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    mockFetch.mockResolvedValue([])
    renderComponent()

    await user.click(await screen.findByRole('button', { name: /Clear all memory/ }))

    expect(mockClearAll).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('exports memory as a downloadable JSON blob', async () => {
    const user = userEvent.setup()
    const createUrl = vi.fn(() => 'blob:mock')
    const revokeUrl = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: createUrl, revokeObjectURL: revokeUrl })
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    mockFetch.mockResolvedValue([])
    renderComponent()

    await user.click(await screen.findByRole('button', { name: /Export my memory/ }))

    await waitFor(() => expect(mockExport).toHaveBeenCalledWith('test-token'))
    expect(createUrl).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('shows an export error when the export fails', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockExport.mockRejectedValue(new Error('nope'))
    renderComponent()

    await user.click(await screen.findByRole('button', { name: /Export my memory/ }))

    expect(await screen.findByText('Export failed. Please try again.')).toBeInTheDocument()
  })
})
