import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
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
})

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
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, payload] = mockSave.mock.calls[0]
    expect(payload.summary).toBe('New summary')
    // Coach-owned fields are never part of the edit payload.
    expect(payload).not.toHaveProperty('confidence')
    expect(payload).not.toHaveProperty('updatedAt')
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

    await screen.findByDisplayValue('260')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

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

    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, payload] = mockSave.mock.calls[0]
    expect(payload.ftpWatts).toBeNull()
    expect(payload.strengths).toEqual(['climbing', 'sprinting'])
  })
})
