import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AthleteModelSettings from './AthleteModelSettings'
import type { AthleteModel } from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())
const mockSave = vi.hoisted(() => vi.fn())
const mockRefresh = vi.hoisted(() => vi.fn())

vi.mock('../services/user', () => ({
  fetchAthleteModel: mockFetch,
  saveAthleteModel: mockSave,
}))

vi.mock('../services/ai', () => ({
  refreshAthleteModel: mockRefresh,
}))

// The interface language and the plan's translations (ai-trainer-ops#258).
const mockUi = vi.hoisted(() => ({
  language: 'auto',
  translations: {} as Record<string, string>,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (
    selector: (state: {
      authToken: string
      uiLanguage: string
      planTranslations: Record<string, string>
    }) => unknown
  ) =>
    selector({
      authToken: 'test-token',
      uiLanguage: mockUi.language,
      planTranslations: mockUi.translations,
    }),
}))

function makeModel(overrides: Partial<AthleteModel> = {}): AthleteModel {
  return {
    ftpWatts: 260,
    vo2max: 58,
    pacingQuality: 'even pacing',
    recoveryAbility: 'recovers fast',
    thresholdDurability: 'holds 30 min',
    heatTolerance: '',
    preferredTrainingStyle: 'intervals',
    strengths: ['threshold'],
    weaknesses: ['sprint'],
    riskFactors: [],
    summary: 'Durable threshold rider.',
    confidence: 0.7,
    updatedAt: '2026-07-10T00:00:00Z',
    ...overrides,
  }
}

function renderComponent() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AthleteModelSettings />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUi.language = 'auto'
  mockUi.translations = {}
})

/** Leave the form, which is when the model saves (ai-trainer-ops#93). */
async function leave() {
  await act(async () => {
    ;(document.activeElement as HTMLElement | null)?.blur()
  })
}

describe('AthleteModelSettings', () => {
  it('renders the loaded model into the editable form', async () => {
    mockFetch.mockResolvedValue(makeModel())
    renderComponent()

    expect(await screen.findByDisplayValue('260')).toBeInTheDocument()
    expect(screen.getByDisplayValue('holds 30 min')).toBeInTheDocument()
    // List fields are shown one item per line.
    expect(screen.getByDisplayValue('threshold')).toBeInTheDocument()
    expect(screen.getByText(/Coach confidence: 70%/)).toBeInTheDocument()
  })

  it('saves athlete edits via saveAthleteModel', async () => {
    mockFetch.mockResolvedValue(makeModel())
    mockSave.mockResolvedValue(makeModel({ summary: 'edited' }))
    renderComponent()

    const summary = await screen.findByDisplayValue('Durable threshold rider.')
    await userEvent.clear(summary)
    await userEvent.type(summary, 'New summary')
    await leave()

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, payload] = mockSave.mock.calls[0]
    expect(payload.summary).toBe('New summary')
    // Coach-owned fields are never part of the edit payload.
    expect(payload).not.toHaveProperty('confidence')
    expect(payload).not.toHaveProperty('updatedAt')
  })

  it('sends nothing when the form is left unchanged', async () => {
    mockFetch.mockResolvedValue(makeModel())
    renderComponent()

    await userEvent.click(await screen.findByDisplayValue('260'))
    await leave()

    expect(mockSave).not.toHaveBeenCalled()
  })

  it("shows the coach's texts in German and saves only what the athlete typed in their words (ai-trainer-ops#258)", async () => {
    mockUi.language = 'de'
    mockUi.translations = {
      'even pacing': 'gleichmäßiges Pacing',
      threshold: 'Schwelle',
      'Durable threshold rider.': 'Ausdauernder Schwellenfahrer.',
    }
    mockFetch.mockResolvedValue(makeModel())
    mockSave.mockResolvedValue(makeModel())
    renderComponent()

    expect(await screen.findByDisplayValue('gleichmäßiges Pacing')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Schwelle')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Ausdauernder Schwellenfahrer.')).toBeInTheDocument()
    // A text without a translation yet shows as the coach wrote it.
    const recovery = screen.getByDisplayValue('recovers fast')

    await userEvent.clear(recovery)
    await userEvent.type(recovery, 'erholt sich schnell')
    await userEvent.type(screen.getByDisplayValue('Schwelle'), '{End}{Enter}Sprints')
    await leave()

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, payload] = mockSave.mock.calls[0]
    expect(payload.recoveryAbility).toBe('erholt sich schnell')
    expect(payload.strengths).toEqual(['threshold', 'Sprints'])
    // What the athlete left alone goes back as the coach wrote it.
    expect(payload.pacingQuality).toBe('even pacing')
    expect(payload.summary).toBe('Durable threshold rider.')
  })

  it('keeps a space typed after a text the coach wrote (review of #1339)', async () => {
    mockFetch.mockResolvedValue(makeModel())
    renderComponent()

    await userEvent.type(await screen.findByDisplayValue('recovers fast'), ' and well')
    await userEvent.type(screen.getByDisplayValue('threshold'), ' work')

    expect(screen.getByDisplayValue('recovers fast and well')).toBeInTheDocument()
    expect(screen.getByDisplayValue('threshold work')).toBeInTheDocument()
  })

  it('sends nothing when a German form is left unchanged (ai-trainer-ops#258)', async () => {
    mockUi.language = 'de'
    mockUi.translations = { 'even pacing': 'gleichmäßiges Pacing' }
    mockFetch.mockResolvedValue(makeModel())
    renderComponent()

    await userEvent.click(await screen.findByDisplayValue('gleichmäßiges Pacing'))
    await leave()

    expect(mockSave).not.toHaveBeenCalled()
  })

  it('refreshes the model from training history', async () => {
    mockFetch.mockResolvedValue(makeModel({ ftpWatts: 250 }))
    mockRefresh.mockResolvedValue(makeModel({ ftpWatts: 275 }))
    renderComponent()

    await screen.findByDisplayValue('250')
    await userEvent.click(
      screen.getByRole('button', { name: /refresh from training/i })
    )

    await waitFor(() => expect(mockRefresh).toHaveBeenCalledWith('test-token'))
    expect(await screen.findByDisplayValue('275')).toBeInTheDocument()
  })

  it('shows an error when the model fails to load', async () => {
    mockFetch.mockRejectedValue(new Error('boom'))
    renderComponent()

    expect(
      await screen.findByText(/could not load the athlete model/i)
    ).toBeInTheDocument()
  })

  it('surfaces a save failure', async () => {
    mockFetch.mockResolvedValue(makeModel())
    mockSave.mockRejectedValue(new Error('nope'))
    renderComponent()

    await userEvent.type(await screen.findByDisplayValue('260'), '5')
    await leave()

    expect(
      await screen.findByText(/could not save the athlete model/i)
    ).toBeInTheDocument()
  })

  it('surfaces a refresh failure', async () => {
    mockFetch.mockResolvedValue(makeModel())
    mockRefresh.mockRejectedValue(new Error('nope'))
    renderComponent()

    await screen.findByDisplayValue('260')
    await userEvent.click(
      screen.getByRole('button', { name: /refresh from training/i })
    )

    expect(
      await screen.findByText(/could not refresh from training history/i)
    ).toBeInTheDocument()
  })

  it('clears a numeric field to null and edits list fields', async () => {
    mockFetch.mockResolvedValue(makeModel())
    mockSave.mockImplementation((_token, payload) => Promise.resolve(makeModel(payload)))
    renderComponent()

    const ftp = await screen.findByDisplayValue('260')
    await userEvent.clear(ftp)

    const strengths = screen.getByDisplayValue('threshold')
    await userEvent.clear(strengths)
    await userEvent.type(strengths, 'climbing{enter}sprinting')

    await leave()

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, payload] = mockSave.mock.calls[0]
    expect(payload.ftpWatts).toBeNull()
    expect(payload.strengths).toEqual(['climbing', 'sprinting'])
  })
})

describe('AthleteModelSettings — the other side of each line (ai-trainer-ops#104)', () => {
  it('leaves the date out of a model that was never updated', async () => {
    mockFetch.mockResolvedValue(makeModel({ updatedAt: null }))
    renderComponent()
    expect(await screen.findByText('Coach confidence: 70%')).toBeInTheDocument()
    expect(screen.queryByText(/updated/)).not.toBeInTheDocument()
  })

  it('says it is saving while an edit is stored', async () => {
    mockFetch.mockResolvedValue(makeModel())
    mockSave.mockReturnValue(new Promise(() => {}))
    renderComponent()
    const summary = await screen.findByDisplayValue('Durable threshold rider.')
    await userEvent.type(summary, ' Edited.')
    await leave()
    expect(await screen.findByText('Saving…')).toBeInTheDocument()
  })
})
