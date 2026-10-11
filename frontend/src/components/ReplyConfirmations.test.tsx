import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReplyConfirmations from './ReplyConfirmations'
import { useAppStore } from '../store/useAppStore'
import type { ChatMessage } from '../store/useAppStore'

const mocks = vi.hoisted(() => ({
  removeGoalRemote: vi.fn(),
  clearProfileRaceRemote: vi.fn(),
  fetchCurrentUser: vi.fn(),
}))
vi.mock('../services/user', () => mocks)

function reply(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return { role: 'assistant', content: 'Noted.', timestamp: '2026-10-11T10:00:00Z', ...overrides }
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'test-token' })
  mocks.removeGoalRemote.mockResolvedValue(undefined)
  mocks.clearProfileRaceRemote.mockResolvedValue(undefined)
  mocks.fetchCurrentUser.mockResolvedValue({ profile: { name: 'Rider' } })
})

describe('ReplyConfirmations', () => {
  it('renders nothing for an ordinary reply', () => {
    const { container } = render(<ReplyConfirmations msg={reply()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a captured goal and takes it back on Remove', async () => {
    render(
      <ReplyConfirmations msg={reply({ capturedGoals: [{ id: 'g1', label: '4,000 m of climbing in one ride' }] })} />,
    )
    expect(screen.getByText('4,000 m of climbing in one ride')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(mocks.removeGoalRemote).toHaveBeenCalledWith('test-token', 'g1')
    expect(await screen.findByText('Removed.')).toBeInTheDocument()
  })

  it('keeps a goal without calling the server', async () => {
    render(<ReplyConfirmations msg={reply({ capturedGoals: [{ id: 'g1', label: '200 km in one ride' }] })} />)

    await userEvent.click(screen.getByRole('button', { name: /keep/i }))

    expect(mocks.removeGoalRemote).not.toHaveBeenCalled()
    expect(screen.getByText('Kept.')).toBeInTheDocument()
  })

  it('only proposes clearing the race, and clears it when the athlete confirms', async () => {
    render(
      <ReplyConfirmations msg={reply({ raceProposal: { raceDate: '2027-05-13', raceDescription: '80km 4000hm' } })} />,
    )
    expect(screen.getByText(/Remove the race from your profile \(80km 4000hm, 2027-05-13\)\?/)).toBeInTheDocument()
    expect(mocks.clearProfileRaceRemote).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /remove race/i }))

    expect(mocks.clearProfileRaceRemote).toHaveBeenCalledWith('test-token')
    expect(await screen.findByText(/plans without a race/)).toBeInTheDocument()
    await waitFor(() => expect(useAppStore.getState().userProfile).toEqual({ name: 'Rider' }))
  })

  it('says that upcoming calendar races go too', () => {
    render(
      <ReplyConfirmations
        msg={reply({ raceProposal: { raceDate: '2027-05-13', raceDescription: 'Calendar race', calendarRaces: 2 } })}
      />,
    )
    expect(screen.getByText(/and 2 upcoming races in your calendar/)).toBeInTheDocument()
  })

  it('says so when the server refuses', async () => {
    mocks.removeGoalRemote.mockRejectedValue(new Error('404'))
    render(<ReplyConfirmations msg={reply({ capturedGoals: [{ id: 'g1', label: '200 km in one ride' }] })} />)

    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(await screen.findByText(/Could not remove it/)).toBeInTheDocument()
  })
})
