import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MotivationModelSettings from './MotivationModelSettings'
import type { AthleteMotivationModel, MotivationEntry } from '../services/user'

const mockFetch = vi.hoisted(() => vi.fn())
const mockUpdate = vi.hoisted(() => vi.fn())

vi.mock('../services/user', () => ({
  fetchMotivationModel: mockFetch,
  updateMotivationModel: mockUpdate,
}))

vi.mock('../store/useAppStore', () => ({
  useAppStore: (selector: (state: { authToken: string }) => unknown) =>
    selector({ authToken: 'test-token' }),
}))

function makeEntry(overrides: Partial<MotivationEntry> = {}): MotivationEntry {
  return {
    text: 'Improve climbing speed',
    confidence: 0.45,
    source: 'inferred',
    sourceSnippet: 'I want to climb better',
    status: 'active',
    contradictionNote: null,
    firstObservedAt: '2026-07-01T00:00:00Z',
    lastConfirmedAt: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function makeModel(
  overrides: Partial<AthleteMotivationModel> = {},
): AthleteMotivationModel {
  return {
    primaryObjective: 'Maximize enjoyable technical trail riding',
    primaryObjectiveSource: 'inferred',
    primaryObjectiveConfidence: 0.62,
    primaryObjectiveSnippet: 'I want more trail time',
    secondaryObjectives: [makeEntry()],
    constraints: [makeEntry({ text: 'Stay healthy', source: 'user_set', confidence: 1 })],
    utilityWeights: {
      enjoyment: 0.45,
      adaptation: 0.25,
      consistency: 0.15,
      health: 0.1,
      race_performance: 0.05,
    },
    pinnedWeights: [],
    modalityAffinity: { road: 0.3, mtb: 0.85, gravel: 0.5, indoor: 0.4, gym: 0.5 },
    updatedAt: '2026-08-12T00:00:00Z',
    ...overrides,
  }
}

function renderComponent() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MotivationModelSettings />
    </QueryClientProvider>,
  )
}

describe('MotivationModelSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockResolvedValue(makeModel())
    mockUpdate.mockImplementation(async () => makeModel())
  })

  // -------------------------------------------------------------------------
  // Visible
  // -------------------------------------------------------------------------

  it('shows the objective the coach is planning against', async () => {
    renderComponent()

    await waitFor(() => {
      expect(
        screen.getByDisplayValue('Maximize enjoyable technical trail riding'),
      ).toBeInTheDocument()
    })
    expect(screen.getByDisplayValue('Improve climbing speed')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Stay healthy')).toBeInTheDocument()
  })

  it('shows the evidence behind an inferred objective', async () => {
    renderComponent()

    // An inferred claim must never read as a fact: confidence and the athlete's
    // own words are what make it correctable rather than mysterious.
    await waitFor(() => {
      expect(screen.getByText(/62% confidence/)).toBeInTheDocument()
    })
    expect(screen.getByText(/I want more trail time/)).toBeInTheDocument()
  })

  it('marks what the athlete set themselves as protected', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('You set this.')).toBeInTheDocument()
    })
    expect(
      screen.getByText(/will not overwrite it/),
    ).toBeInTheDocument()
  })

  it('explains the no-objective state instead of leaving a blank box', async () => {
    mockFetch.mockResolvedValue(
      makeModel({ primaryObjective: '', primaryObjectiveConfidence: 0 }),
    )
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText(/Nothing inferred yet/)).toBeInTheDocument()
    })
  })

  it('shows the weight balance as percentages', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('45%')).toBeInTheDocument()
    })
    expect(screen.getByLabelText('Enjoyment weight')).toHaveValue('45')
  })

  it('shows what was learned about how the athlete likes to ride', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText(/MTB · 85%/)).toBeInTheDocument()
    })
  })

  it('surfaces a contradicted objective for the athlete to settle', async () => {
    mockFetch.mockResolvedValue(
      makeModel({
        secondaryObjectives: [
          makeEntry({
            text: 'Perform at races',
            status: 'contradicted',
            contradictionNote: 'No race on your calendar. Still the goal?',
          }),
        ],
      }),
    )
    renderComponent()

    await waitFor(() => {
      expect(
        screen.getByText('No race on your calendar. Still the goal?'),
      ).toBeInTheDocument()
    })
  })

  // -------------------------------------------------------------------------
  // Editable
  // -------------------------------------------------------------------------

  it('saves a rewritten primary objective', async () => {
    renderComponent()
    await waitFor(() => {
      expect(screen.getByLabelText('Primary objective')).toBeInTheDocument()
    })

    const input = screen.getByLabelText('Primary objective')
    await userEvent.clear(input)
    await userEvent.type(input, 'Ride the Trans-Alp')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(
        'test-token',
        expect.objectContaining({ primaryObjective: 'Ride the Trans-Alp' }),
      )
    })
  })

  it('sends reordered objectives in the order the athlete chose', async () => {
    mockFetch.mockResolvedValue(
      makeModel({
        secondaryObjectives: [
          makeEntry({ text: 'Improve climbing speed' }),
          makeEntry({ text: 'Complete marathon races' }),
        ],
      }),
    )
    renderComponent()
    await waitFor(() => {
      expect(
        screen.getByDisplayValue('Complete marathon races'),
      ).toBeInTheDocument()
    })

    await userEvent.click(
      screen.getByLabelText('Move “Complete marathon races” up'),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    const sent = mockUpdate.mock.calls[0][1]
    expect(sent.secondaryObjectives.map((e: MotivationEntry) => e.text)).toEqual([
      'Complete marathon races',
      'Improve climbing speed',
    ])
  })

  it('removes a constraint the athlete deletes', async () => {
    renderComponent()
    await waitFor(() => {
      expect(screen.getByDisplayValue('Stay healthy')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByLabelText('Remove “Stay healthy”'))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    expect(mockUpdate.mock.calls[0][1].constraints).toEqual([])
  })

  it('pins a weight so learning leaves it alone', async () => {
    renderComponent()
    await waitFor(() => {
      expect(screen.getByLabelText('Pin Enjoyment')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByLabelText('Pin Enjoyment'))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(
        'test-token',
        expect.objectContaining({ pinnedWeights: ['enjoyment'] }),
      )
    })
  })

  it("marks an edited entry as the athlete's own wording", async () => {
    renderComponent()
    await waitFor(() => {
      expect(screen.getByDisplayValue('Improve climbing speed')).toBeInTheDocument()
    })

    await userEvent.type(screen.getByLabelText('Objective 1'), ' a lot')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    // Editing it makes it theirs, which is what protects it from inference.
    expect(mockUpdate.mock.calls[0][1].secondaryObjectives[0].source).toBe('user_set')
  })

  it('drops a half-finished blank row instead of saving it', async () => {
    renderComponent()
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Add constraint/ }),
      ).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /Add constraint/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    expect(mockUpdate.mock.calls[0][1].constraints).toHaveLength(1)
  })

  // -------------------------------------------------------------------------
  // Save behaviour
  // -------------------------------------------------------------------------

  it('keeps Save disabled until something actually changed', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    })

    await userEvent.type(screen.getByLabelText('Primary objective'), '!')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('shows the normalised model the server returns, not the local guess', async () => {
    // The backend re-balances the weights to 100% on save; showing the draft
    // instead would leave the athlete looking at numbers that are not stored.
    mockUpdate.mockResolvedValue(
      makeModel({ primaryObjective: 'Server normalised objective' }),
    )
    renderComponent()
    await waitFor(() => {
      expect(screen.getByLabelText('Primary objective')).toBeInTheDocument()
    })

    await userEvent.type(screen.getByLabelText('Primary objective'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(
        screen.getByDisplayValue('Server normalised objective'),
      ).toBeInTheDocument()
    })
  })

  it('reports a failed save instead of pretending it worked', async () => {
    mockUpdate.mockRejectedValue(new Error('nope'))
    renderComponent()
    await waitFor(() => {
      expect(screen.getByLabelText('Primary objective')).toBeInTheDocument()
    })

    await userEvent.type(screen.getByLabelText('Primary objective'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(screen.getByText(/Could not save/)).toBeInTheDocument()
    })
  })

  it('discards changes back to the stored model', async () => {
    renderComponent()
    await waitFor(() => {
      expect(screen.getByLabelText('Primary objective')).toBeInTheDocument()
    })

    await userEvent.type(screen.getByLabelText('Primary objective'), ' xyz')
    await userEvent.click(screen.getByRole('button', { name: /Discard changes/ }))

    expect(
      screen.getByDisplayValue('Maximize enjoyable technical trail riding'),
    ).toBeInTheDocument()
  })

  it('reports a load failure', async () => {
    mockFetch.mockRejectedValue(new Error('boom'))
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText(/Could not load your objective/)).toBeInTheDocument()
    })
  })
})
