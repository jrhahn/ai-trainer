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

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
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
    mockExtract.mockResolvedValue([makeCandidate(), makeCandidate({ fact: 'Loves climbing', category: 'preferred_workouts' })])
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
    mockExtract.mockResolvedValue([makeCandidate()])
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
    mockExtract.mockResolvedValue([makeCandidate()])
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
    mockExtract.mockResolvedValue([])
    renderComponent()

    await userEvent.type(
      screen.getByRole('textbox', { name: /conversation transcript/i }),
      'small talk only',
    )
    await userEvent.click(screen.getByRole('button', { name: /extract traits/i }))

    expect(await screen.findByText(/No durable traits found/i)).toBeInTheDocument()
  })
})
