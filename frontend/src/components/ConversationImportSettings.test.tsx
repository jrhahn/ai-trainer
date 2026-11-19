import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ConversationImportSettings from './ConversationImportSettings'
import type { AthleteFactCandidate } from '../services/ai'

const mockExtract = vi.hoisted(() => vi.fn())
const mockObserve = vi.hoisted(() => vi.fn())

vi.mock('../services/ai', () => ({
  extractAthleteFacts: mockExtract,
}))

vi.mock('../services/user', () => ({
  observeAthleteMemoryFact: mockObserve,
}))

// The interface language and the plan's translations (ai-trainer-ops#258).
const store = vi.hoisted(() => ({
  uiLanguage: 'auto',
  planTranslations: {} as Record<string, string>,
  mergePlanTranslations: vi.fn(),
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (
    selector: (state: {
      authToken: string
      uiLanguage: string
      planTranslations: Record<string, string>
      mergePlanTranslations: (translations: Record<string, string>) => void
    }) => unknown,
  ) => selector({ authToken: 'test-token', ...store }),
}))

function makeCandidate(overrides: Partial<AthleteFactCandidate> = {}): AthleteFactCandidate {
  return {
    fact: 'Gets anxious after two rest days',
    category: 'psychological_tendencies',
    confidence: 0.7,
    sourceSnippet: 'I feel like I lose fitness when I rest.',
    ...overrides,
  }
}

function renderComponent() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <ConversationImportSettings />
    </QueryClientProvider>,
  )
}

describe('ConversationImportSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    store.uiLanguage = 'auto'
    store.planTranslations = {}
  })

  it("shows the candidates in German and keeps their translations (ai-trainer-ops#258)", async () => {
    const translations = { 'Gets anxious after two rest days': 'Wirst nach zwei Ruhetagen unruhig' }
    store.uiLanguage = 'de'
    // The store keeps what the answer brought; the next render reads it.
    store.mergePlanTranslations.mockImplementation((more: Record<string, string>) => {
      store.planTranslations = { ...store.planTranslations, ...more }
    })
    mockExtract.mockResolvedValue({ candidates: [makeCandidate()], translations })
    mockObserve.mockResolvedValue({})
    renderComponent()

    await userEvent.type(screen.getByRole('textbox'), 'Coach: wie geht es?')
    await userEvent.click(screen.getByRole('button', { name: 'Eigenschaften herausziehen' }))

    expect(await screen.findByText('Wirst nach zwei Ruhetagen unruhig')).toBeInTheDocument()
    expect(store.mergePlanTranslations).toHaveBeenCalledWith(translations)
    // What is kept is the English fact, as the coach reads it.
    await userEvent.click(screen.getByRole('button', { name: 'Vorschlag übernehmen' }))
    await waitFor(() => expect(mockObserve).toHaveBeenCalled())
    expect(mockObserve.mock.calls[0][1].fact).toBe('Gets anxious after two rest days')
  })

  it('disables extract until a transcript is entered', async () => {
    renderComponent()
    const button = screen.getByRole('button', { name: /extract traits/i })
    expect(button).toBeDisabled()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'Athlete: I hate resting.',
    )
    expect(button).toBeEnabled()
  })

  it('extracts and lists candidates for review without persisting', async () => {
    mockExtract.mockResolvedValue({ candidates: [makeCandidate(), makeCandidate({ fact: 'Loves climbing', category: 'preferred_workouts' })], translations: {} })
    renderComponent()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'Athlete: I hate resting.',
    )
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))

    expect(await screen.findByText('Gets anxious after two rest days')).toBeInTheDocument()
    expect(screen.getByText('Loves climbing')).toBeInTheDocument()
    expect(screen.getByText(/Review candidates \(2\)/)).toBeInTheDocument()
    // Extraction alone persists nothing.
    expect(mockObserve).not.toHaveBeenCalled()
    expect(mockExtract).toHaveBeenCalledWith('Athlete: I hate resting.', 'test-token')
  })

  it('accepts a candidate, persisting it and removing it from the list', async () => {
    mockExtract.mockResolvedValue({ candidates: [makeCandidate()], translations: {} })
    mockObserve.mockResolvedValue({ id: 'f1' })
    renderComponent()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'transcript',
    )
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))

    const row = (await screen.findByText('Gets anxious after two rest days')).closest('li')!
    await userEvent.click(within(row).getByRole('button', { name: /accept candidate/i }))

    await waitFor(() =>
      expect(mockObserve).toHaveBeenCalledWith('test-token', {
        fact: 'Gets anxious after two rest days',
        category: 'psychological_tendencies',
        sourceSnippet: 'I feel like I lose fitness when I rest.',
        confidence: 0.7,
      }),
    )
    await waitFor(() =>
      expect(screen.queryByText('Gets anxious after two rest days')).toBeNull(),
    )
  })

  it('rejects a candidate without persisting', async () => {
    mockExtract.mockResolvedValue({ candidates: [makeCandidate()], translations: {} })
    renderComponent()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'transcript',
    )
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))

    const row = (await screen.findByText('Gets anxious after two rest days')).closest('li')!
    await userEvent.click(within(row).getByRole('button', { name: /reject candidate/i }))

    expect(screen.queryByText('Gets anxious after two rest days')).toBeNull()
    expect(mockObserve).not.toHaveBeenCalled()
  })

  it('shows an empty-result message when no traits are found', async () => {
    mockExtract.mockResolvedValue({ candidates: [], translations: {} })
    renderComponent()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'small talk only',
    )
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))

    expect(await screen.findByText(/No durable traits found/i)).toBeInTheDocument()
  })
})

describe('ConversationImportSettings — failures and waiting (ai-trainer-ops#104)', () => {
  beforeEach(() => vi.clearAllMocks())

  async function extract() {
    renderComponent()
    await userEvent.type(screen.getByRole('textbox', { name: /conversation transcript/i }), 'Athlete: I hate resting.')
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))
  }

  it('says it is extracting while the coach reads', async () => {
    mockExtract.mockReturnValue(new Promise(() => {}))
    await extract()
    expect(await screen.findByRole('button', { name: /Extracting…/ })).toBeInTheDocument()
  })

  it('shows why extracting failed', async () => {
    mockExtract.mockRejectedValue(new Error('extract down'))
    await extract()
    expect(await screen.findByText('extract down')).toBeInTheDocument()
  })

  it('shows why keeping a candidate failed', async () => {
    mockExtract.mockResolvedValue({ candidates: [makeCandidate()], translations: {} })
    mockObserve.mockRejectedValue(new Error('save down'))
    await extract()
    await userEvent.click(await screen.findByRole('button', { name: 'Accept candidate' }))
    expect(await screen.findByText('save down')).toBeInTheDocument()
  })
})
