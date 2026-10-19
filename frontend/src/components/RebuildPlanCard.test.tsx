import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RebuildPlanCard, { dismissedKey } from './RebuildPlanCard'
import { useAppStore, type UserProfile } from '../store/useAppStore'

const { mockChanged, mockGenerate } = vi.hoisted(() => ({
  mockChanged: vi.fn(),
  mockGenerate: vi.fn(),
}))
vi.mock('../services/ai', () => ({
  fetchPlanInputsChanged: mockChanged,
  generateTrainingPlan: mockGenerate,
}))

const plan = [{ date: '2026-10-21', workoutType: 'endurance', title: 'Easy ride', description: '', durationMinutes: 60 }]

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok', userProfile: { name: 'Mara', email: 'mara@example.com' } as UserProfile })
  localStorage.clear()
  vi.clearAllMocks()
})

describe('RebuildPlanCard (ai-trainer-ops#158)', () => {
  it('names what moved and rebuilds the plan on request', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['history', 'assumptions'] })
    mockGenerate.mockResolvedValue(plan)
    render(<RebuildPlanCard />)

    expect(
      await screen.findByText(/Your coach knows more about you now: there are new rides on record, you answered my questions about you\./),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Rebuild my plan' }))

    expect(mockGenerate).toHaveBeenCalledWith('tok')
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Rebuild your plan' })).not.toBeInTheDocument())
    expect(useAppStore.getState().trainingPlan).toEqual(plan)
  })

  it('shows nothing when nothing moved', async () => {
    mockChanged.mockResolvedValue({ changed: false, reasons: [] })
    const { container } = render(<RebuildPlanCard />)

    await waitFor(() => expect(mockChanged).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('stays quiet when the question fails', async () => {
    mockChanged.mockRejectedValue(new Error('offline'))
    const { container } = render(<RebuildPlanCard />)

    await waitFor(() => expect(mockChanged).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('keeps the offer and says so when the rebuild fails', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['ftp'] })
    mockGenerate.mockRejectedValue(new Error('The AI model did not answer just now.'))
    render(<RebuildPlanCard />)

    await userEvent.click(await screen.findByRole('button', { name: 'Rebuild my plan' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The AI model did not answer just now.')
    expect(screen.getByRole('button', { name: 'Rebuild my plan' })).toBeEnabled()
  })

  it('stays dismissed until the reasons change', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['ftp'] })
    const first = render(<RebuildPlanCard />)

    await userEvent.click(await screen.findByRole('button', { name: 'Not now' }))

    expect(screen.queryByRole('region', { name: 'Rebuild your plan' })).not.toBeInTheDocument()
    expect(localStorage.getItem(dismissedKey('mara@example.com'))).toBe('ftp')
    first.unmount()

    render(<RebuildPlanCard />)
    await waitFor(() => expect(mockChanged).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('region', { name: 'Rebuild your plan' })).not.toBeInTheDocument()

    mockChanged.mockResolvedValue({ changed: true, reasons: ['ftp', 'race'] })
    useAppStore.setState({ userProfile: { name: 'Mara', email: 'mara@example.com', raceDate: '2027-05-12' } as UserProfile })
    expect(await screen.findByRole('region', { name: 'Rebuild your plan' })).toHaveTextContent('your race changed')
  })

  it("does not hide another account's offer on the same browser", async () => {
    localStorage.setItem(dismissedKey('someone@example.com'), 'ftp')
    mockChanged.mockResolvedValue({ changed: true, reasons: ['ftp'] })
    render(<RebuildPlanCard />)

    expect(await screen.findByRole('region', { name: 'Rebuild your plan' })).toBeInTheDocument()
  })

  it('asks nothing without a session', () => {
    useAppStore.setState({ authToken: null })
    render(<RebuildPlanCard />)

    expect(mockChanged).not.toHaveBeenCalled()
  })
})
