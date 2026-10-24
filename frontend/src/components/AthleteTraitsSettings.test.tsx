import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AthleteTraitsSettings from './AthleteTraitsSettings'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteMemoryFact,
  AthleteOpenQuestion,
  AthletePrediction,
  AthletePredictionsResult,
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
const mockFetchOpenQuestions = vi.hoisted(() => vi.fn())
const mockAnswerOpenQuestion = vi.hoisted(() => vi.fn())
const mockDismissOpenQuestion = vi.hoisted(() => vi.fn())
const mockDeleteOpenQuestion = vi.hoisted(() => vi.fn())
const mockFetchExperiments = vi.hoisted(() => vi.fn())
const mockCompleteExperiment = vi.hoisted(() => vi.fn())
const mockDismissExperiment = vi.hoisted(() => vi.fn())
const mockDeleteExperiment = vi.hoisted(() => vi.fn())
const mockFetchPredictions = vi.hoisted(() => vi.fn())
const mockMarkPredictionCorrect = vi.hoisted(() => vi.fn())
const mockMarkPredictionIncorrect = vi.hoisted(() => vi.fn())
const mockDeletePrediction = vi.hoisted(() => vi.fn())

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
  fetchAthleteOpenQuestions: mockFetchOpenQuestions,
  answerAthleteOpenQuestion: mockAnswerOpenQuestion,
  dismissAthleteOpenQuestion: mockDismissOpenQuestion,
  deleteAthleteOpenQuestion: mockDeleteOpenQuestion,
  fetchValidationExperiments: mockFetchExperiments,
  completeValidationExperiment: mockCompleteExperiment,
  dismissValidationExperiment: mockDismissExperiment,
  deleteValidationExperiment: mockDeleteExperiment,
  fetchAthletePredictions: mockFetchPredictions,
  markPredictionCorrect: mockMarkPredictionCorrect,
  markPredictionIncorrect: mockMarkPredictionIncorrect,
  deleteAthletePrediction: mockDeletePrediction,
}))

// Apply the selector so `useAppStore((s) => s.authToken)` returns the token.
// The interface language is read through the same store (ai-trainer-ops#104).
const mockUiLanguage = vi.hoisted(() => ({ value: 'auto' }))
// The coach's working state shows in expert mode only (owner's review,
// 2026-10-23); most tests below are about it, so they run as an expert.
const mockExpert = vi.hoisted(() => ({ value: true }))
// The coach's texts in German, from the plan's translations (ai-trainer-ops#215).
const mockTranslations = vi.hoisted(() => ({ value: {} as Record<string, string> }))
vi.mock('../store/useAppStore', () => ({
  useAppStore: (
    selector: (state: {
      authToken: string
      uiLanguage: string
      isExpertMode: boolean
      planTranslations: Record<string, string>
    }) => unknown,
  ) =>
    selector({
      authToken: 'test-token',
      uiLanguage: mockUiLanguage.value,
      isExpertMode: mockExpert.value,
      planTranslations: mockTranslations.value,
    }),
}))

function makeFact(overrides: Partial<AthleteMemoryFact> = {}): AthleteMemoryFact {
  return {
    id: 'fact-1',
    fact: 'Adds extra work after rest days',
    kind: 'observation',
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
    evidence: [],
    alternativeExplanations: [],
    evidenceCount: 2,
    status: 'proposed',
    firstProposedAt: '2026-06-01T00:00:00Z',
    updatedAt: '2026-06-10T00:00:00Z',
    ...overrides,
  }
}

function makeOpenQuestion(
  overrides: Partial<AthleteOpenQuestion> = {},
): AthleteOpenQuestion {
  return {
    id: 'oq-1',
    question: 'Is FTP underestimated?',
    category: 'general',
    evidence: 'Recent VO2 intervals held above threshold.',
    needs: '30-minute threshold test.',
    evidenceCount: 2,
    status: 'open',
    resolution: null,
    firstAskedAt: '2026-06-01T00:00:00Z',
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

function makePrediction(
  overrides: Partial<AthletePrediction> = {},
): AthletePrediction {
  return {
    id: 'pred-1',
    prediction: 'The athlete will be fully recovered tomorrow',
    expectedOutcome: 'Resting HR back to baseline',
    actualOutcome: null,
    horizon: 'tomorrow',
    category: 'fatigue_response',
    confidence: 0.6,
    status: 'pending',
    createdAt: '2026-06-01T00:00:00Z',
    evaluatedAt: null,
    updatedAt: '2026-06-10T00:00:00Z',
    ...overrides,
  }
}

function makePredictionsResult(
  overrides: Partial<AthletePredictionsResult> = {},
): AthletePredictionsResult {
  return {
    predictions: [makePrediction()],
    accuracy: { evaluated: 3, correct: 2, accuracy: 2 / 3 },
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
    mockExpert.value = true
    mockFetchPrivacy.mockResolvedValue({ memoryUpdatesEnabled: true })
    mockUpdatePrivacy.mockResolvedValue({ memoryUpdatesEnabled: false })
    mockClearAll.mockResolvedValue(undefined)
    mockExport.mockResolvedValue({ facts: [] })
    mockFetchHypotheses.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue({
      predictions: [],
      accuracy: { evaluated: 0, correct: 0, accuracy: null },
    })
  })

  it('reads German when the interface is German (ai-trainer-ops#104)', async () => {
    mockUiLanguage.value = 'de'
    try {
      mockFetch.mockResolvedValue([makeFact({ status: 'user_confirmed' })])
      renderComponent()

      expect(await screen.findByText('Bestätigt')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Eigenschaft bearbeiten' })).toBeInTheDocument()
      expect(screen.getByText(/Aktualisiert 10\. Juni 2026 · Sicherheit 60 %/)).toBeInTheDocument()
    } finally {
      mockUiLanguage.value = 'auto'
    }
  })

  it("shows the coach's texts in German, and edits a trait in German (ai-trainer-ops#215)", async () => {
    const user = userEvent.setup()
    mockUiLanguage.value = 'de'
    mockTranslations.value = {
      'Adds extra work after rest days': 'Legt nach Ruhetagen zusätzliche Arbeit drauf',
      'Felt fresh after the last rest day.': 'Fühlte sich nach dem letzten Ruhetag frisch an.',
      'Upper-body strength suppresses next-day HR response': 'Oberkörperkraft senkt den Puls am Tag danach',
      'HR ~8 bpm low the day after gym sessions.': 'Puls ~8 bpm niedriger am Tag nach dem Studio.',
      'Is FTP underestimated?': 'Ist die FTP unterschätzt?',
      'Recent VO2 intervals held above threshold.': 'Die letzten VO2-Intervalle lagen über der Schwelle.',
      '30-minute threshold test.': '30-Minuten-Schwellentest.',
      'Does upper-body strength suppress next-day HR?': 'Senkt Oberkörperkraft den Puls am Tag danach?',
      'Repeat the gym session and compare HR on the next easy ride.':
        'Wiederhole die Studio-Einheit und vergleiche den Puls bei der nächsten lockeren Fahrt.',
      'A clear HR drop would confirm it.': 'Ein klarer Pulsabfall würde es bestätigen.',
    }
    try {
      mockFetch.mockResolvedValue([
        makeFact({ status: 'needs_validation', contradictionNote: 'Felt fresh after the last rest day.' }),
      ])
      mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
      mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
      mockFetchExperiments.mockResolvedValue([makeExperiment()])
      renderComponent()

      for (const text of Object.values(mockTranslations.value)) {
        expect(await screen.findByText(text, { exact: false })).toBeInTheDocument()
      }
      for (const text of Object.keys(mockTranslations.value)) {
        expect(screen.queryByText(text, { exact: false })).toBeNull()
      }
      await user.click(screen.getByRole('button', { name: 'Eigenschaft bearbeiten' }))
      expect(screen.getByRole('textbox')).toHaveValue('Legt nach Ruhetagen zusätzliche Arbeit drauf')
      // Saved unchanged, the stored English text stays.
      await user.click(screen.getByRole('button', { name: 'Speichern' }))
      expect(mockUpdate).not.toHaveBeenCalled()
      expect(screen.queryByRole('textbox')).toBeNull()
      // A real change is the athlete's own words, and is stored.
      mockUpdate.mockResolvedValue(makeFact({ fact: 'Legt nach Ruhetagen gern eine lockere Runde ein' }))
      await user.click(screen.getByRole('button', { name: 'Eigenschaft bearbeiten' }))
      await user.clear(screen.getByRole('textbox'))
      await user.type(screen.getByRole('textbox'), 'Legt nach Ruhetagen gern eine lockere Runde ein')
      await user.click(screen.getByRole('button', { name: 'Speichern' }))
      await waitFor(() =>
        expect(mockUpdate).toHaveBeenCalledWith('test-token', 'fact-1', {
          fact: 'Legt nach Ruhetagen gern eine lockere Runde ein',
        }),
      )
    } finally {
      mockUiLanguage.value = 'auto'
      mockTranslations.value = {}
    }
  })

  it('lists traits in one list without category headings', async () => {
    mockFetch.mockResolvedValue([
      makeFact({ id: 'a', category: 'coaching_risk', fact: 'Overreaches when fresh' }),
      makeFact({ id: 'b', category: 'preference', fact: 'Prefers long endurance rides' }),
    ])
    renderComponent()

    expect(await screen.findByText('Overreaches when fresh')).toBeInTheDocument()
    expect(screen.getByText('Prefers long endurance rides')).toBeInTheDocument()
    expect(screen.queryByText('Coaching risk')).toBeNull()
  })

  it('shows five traits and the rest on request', async () => {
    mockFetch.mockResolvedValue(
      Array.from({ length: 7 }, (_, i) => makeFact({ id: `t${i}`, fact: `Trait number ${i}` })),
    )
    renderComponent()

    expect(await screen.findByText('Trait number 4')).toBeInTheDocument()
    expect(screen.queryByText('Trait number 5')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show all 7' }))
    expect(screen.getByText('Trait number 6')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show fewer' }))
    expect(screen.queryByText('Trait number 6')).toBeNull()
  })

  it('keeps the working state and the numbers for expert mode', async () => {
    mockExpert.value = false
    mockFetch.mockResolvedValue([makeFact({ status: 'user_confirmed', fact: 'Rides early' })])
    mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    renderComponent()

    expect(await screen.findByText('Rides early')).toBeInTheDocument()
    expect(screen.getByText('Confirmed')).toBeInTheDocument()
    expect(screen.queryByText(/confidence \d+%/)).toBeNull()
    for (const name of ['Working Hypotheses', 'Open Questions', 'Suggested Experiments', 'Predictions']) {
      expect(screen.queryByRole('heading', { name })).toBeNull()
    }
  })

  it('separates stable facts from behavioural observations', async () => {
    mockFetch.mockResolvedValue([
      makeFact({ id: 'f', kind: 'fact', category: 'general', fact: 'FTP is about 250 W' }),
      makeFact({
        id: 'o',
        kind: 'observation',
        category: 'recurring_issues',
        fact: 'Fades in the final VO2 interval',
      }),
    ])
    renderComponent()

    expect(await screen.findByText('FTP is about 250 W')).toBeInTheDocument()
    expect(screen.getByText('Fades in the final VO2 interval')).toBeInTheDocument()
    // Both section headings render.
    expect(screen.getByRole('heading', { name: 'Facts' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Observations' })).toBeInTheDocument()
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

  it('renders an open question with its evidence and needs', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    renderComponent()

    expect(
      await screen.findByText('Is FTP underestimated?'),
    ).toBeInTheDocument()
    expect(screen.getByText('Open Questions')).toBeInTheDocument()
    expect(
      screen.getByText(/Recent VO2 intervals held above threshold\./),
    ).toBeInTheDocument()
    expect(screen.getByText(/30-minute threshold test\./)).toBeInTheDocument()
    expect(screen.getByText(/2 observations/)).toBeInTheDocument()
  })

  it('answers an open question', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    mockAnswerOpenQuestion.mockResolvedValue(
      makeOpenQuestion({ status: 'answered' }),
    )
    renderComponent()

    await screen.findByText('Is FTP underestimated?')
    await user.click(screen.getByRole('button', { name: /answer question/i }))

    await waitFor(() =>
      expect(mockAnswerOpenQuestion).toHaveBeenCalledWith('test-token', 'oq-1'),
    )
  })

  it('dismisses an open question', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    mockDismissOpenQuestion.mockResolvedValue(
      makeOpenQuestion({ status: 'dismissed' }),
    )
    renderComponent()

    await screen.findByText('Is FTP underestimated?')
    await user.click(screen.getByRole('button', { name: /dismiss question/i }))

    await waitFor(() =>
      expect(mockDismissOpenQuestion).toHaveBeenCalledWith('test-token', 'oq-1'),
    )
  })

  it('deletes an open question after confirmation', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    mockDeleteOpenQuestion.mockResolvedValue(undefined)
    renderComponent()

    await screen.findByText('Is FTP underestimated?')
    await user.click(screen.getByRole('button', { name: /delete question/i }))

    await waitFor(() =>
      expect(mockDeleteOpenQuestion).toHaveBeenCalledWith('test-token', 'oq-1'),
    )
    confirmSpy.mockRestore()
  })

  it('does not show the open questions section when there are none', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([])
    renderComponent()

    await screen.findByText(/No learned traits yet/i)
    expect(screen.queryByText('Open Questions')).toBeNull()
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

  it('deletes an experiment after confirmation', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockDeleteExperiment.mockResolvedValue(undefined)
    renderComponent()

    await screen.findByText(
      'Repeat the gym session and compare HR on the next easy ride.',
    )
    await user.click(screen.getByRole('button', { name: /delete experiment/i }))

    await waitFor(() =>
      expect(mockDeleteExperiment).toHaveBeenCalledWith('test-token', 'exp-1'),
    )
    confirmSpy.mockRestore()
  })

  it('does not delete an experiment when the confirm dialog is cancelled', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    renderComponent()

    await screen.findByText(
      'Repeat the gym session and compare HR on the next easy ride.',
    )
    await user.click(screen.getByRole('button', { name: /delete experiment/i }))

    expect(mockDeleteExperiment).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('surfaces an error when completing an experiment fails', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockCompleteExperiment.mockRejectedValue(new Error('nope'))
    renderComponent()

    await screen.findByText(
      'Repeat the gym session and compare HR on the next easy ride.',
    )
    await user.click(screen.getByRole('button', { name: /complete experiment/i }))

    expect(await screen.findByText('nope')).toBeInTheDocument()
  })

  it('does not show the experiments section when there are none', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([])
    renderComponent()

    await screen.findByText(/No learned traits yet/i)
    expect(screen.queryByText('Suggested Experiments')).toBeNull()
  })

  it('renders a prediction with its expected outcome and accuracy summary', async () => {
    mockFetch.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    renderComponent()

    expect(
      await screen.findByText('The athlete will be fully recovered tomorrow'),
    ).toBeInTheDocument()
    expect(screen.getByText('Predictions')).toBeInTheDocument()
    expect(
      screen.getByText(/Confirmed if: Resting HR back to baseline/),
    ).toBeInTheDocument()
    expect(screen.getByText(/2 of 3/)).toBeInTheDocument()
    expect(screen.getByText(/67% accuracy/)).toBeInTheDocument()
  })

  it('marks a prediction correct', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    mockMarkPredictionCorrect.mockResolvedValue(
      makePrediction({ status: 'correct' }),
    )
    renderComponent()

    await screen.findByText('The athlete will be fully recovered tomorrow')
    await user.click(
      screen.getByRole('button', { name: /mark prediction correct/i }),
    )

    await waitFor(() =>
      expect(mockMarkPredictionCorrect).toHaveBeenCalledWith('test-token', 'pred-1'),
    )
  })

  it('marks a prediction incorrect', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    mockMarkPredictionIncorrect.mockResolvedValue(
      makePrediction({ status: 'incorrect' }),
    )
    renderComponent()

    await screen.findByText('The athlete will be fully recovered tomorrow')
    await user.click(
      screen.getByRole('button', { name: /mark prediction incorrect/i }),
    )

    await waitFor(() =>
      expect(mockMarkPredictionIncorrect).toHaveBeenCalledWith('test-token', 'pred-1'),
    )
  })

  it('deletes a prediction after confirmation', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockFetch.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    mockDeletePrediction.mockResolvedValue(undefined)
    renderComponent()

    await screen.findByText('The athlete will be fully recovered tomorrow')
    await user.click(screen.getByRole('button', { name: /delete prediction/i }))

    await waitFor(() =>
      expect(mockDeletePrediction).toHaveBeenCalledWith('test-token', 'pred-1'),
    )
    confirmSpy.mockRestore()
  })

  it('does not show the predictions section when there are none', async () => {
    mockFetch.mockResolvedValue([])
    renderComponent()

    await screen.findByText(/No learned traits yet/i)
    expect(screen.queryByText('Predictions')).toBeNull()
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
    // Predictions are cleared too, so the card asks for them again.
    await waitFor(() => expect(mockFetchPredictions).toHaveBeenCalledTimes(2))
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

describe('AthleteTraitsSettings — the other side of each message (ai-trainer-ops#104)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockResolvedValue([])
    mockFetchPrivacy.mockResolvedValue({ memoryUpdatesEnabled: true })
    mockFetchHypotheses.mockResolvedValue([])
    mockFetchOpenQuestions.mockResolvedValue([])
    mockFetchExperiments.mockResolvedValue([])
    mockFetchPredictions.mockResolvedValue({ predictions: [], accuracy: { evaluated: 0, correct: 0, accuracy: null } })
  })

  it('counts one observation in the singular, for a hypothesis and a question', async () => {
    mockFetchHypotheses.mockResolvedValue([makeHypothesis({ evidenceCount: 1 })])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion({ evidenceCount: 1 })])
    renderComponent()

    expect(await screen.findByText(/confidence 38% · 1 observation$/)).toBeInTheDocument()
    expect(screen.getByText('1 observation')).toBeInTheDocument()
  })

  it('shows why a row action failed, in every kind of row', async () => {
    mockFetchHypotheses.mockResolvedValue([makeHypothesis()])
    mockFetchOpenQuestions.mockResolvedValue([makeOpenQuestion()])
    mockFetchExperiments.mockResolvedValue([makeExperiment()])
    mockFetchPredictions.mockResolvedValue(makePredictionsResult())
    mockConfirmHypothesis.mockRejectedValue(new Error('hypothesis down'))
    mockAnswerOpenQuestion.mockRejectedValue(new Error('question down'))
    mockCompleteExperiment.mockRejectedValue(new Error('experiment down'))
    mockMarkPredictionCorrect.mockRejectedValue(new Error('prediction down'))
    renderComponent()

    await userEvent.click(await screen.findByRole('button', { name: 'Confirm hypothesis' }))
    await userEvent.click(screen.getByRole('button', { name: 'Answer question' }))
    await userEvent.click(screen.getByRole('button', { name: 'Complete experiment' }))
    await userEvent.click(screen.getByRole('button', { name: 'Mark prediction correct' }))

    for (const message of ['hypothesis down', 'question down', 'experiment down', 'prediction down']) {
      expect(await screen.findByText(message)).toBeInTheDocument()
    }
  })

  it('leaves out the check date of a prediction without one, and an unknown accuracy reads 0%', async () => {
    mockFetchPredictions.mockResolvedValue(
      makePredictionsResult({
        predictions: [makePrediction({ horizon: undefined })],
        accuracy: { evaluated: 1, correct: 0, accuracy: null },
      }),
    )
    renderComponent()

    expect(await screen.findByText('confidence 60%')).toBeInTheDocument()
    expect(screen.queryByText(/check by/)).not.toBeInTheDocument()
    expect(screen.getByText(/\(0% accuracy\)/)).toBeInTheDocument()
  })
})
