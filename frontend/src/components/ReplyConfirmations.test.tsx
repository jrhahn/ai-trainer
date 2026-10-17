import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReplyConfirmations from './ReplyConfirmations'
import { useAppStore } from '../store/useAppStore'
import type { ChatMessage } from '../store/useAppStore'

const mocks = vi.hoisted(() => ({
  removeGoalRemote: vi.fn(),
  removeStateRemote: vi.fn(),
  removeConstraintRemote: vi.fn(),
  clearProfileRaceRemote: vi.fn(),
  fetchCurrentUser: vi.fn(),
  updateCurrentUser: vi.fn(),
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
  mocks.removeStateRemote.mockResolvedValue(undefined)
  mocks.removeConstraintRemote.mockResolvedValue(undefined)
  mocks.clearProfileRaceRemote.mockResolvedValue(undefined)
  mocks.fetchCurrentUser.mockResolvedValue({ profile: { name: 'Rider' } })
  mocks.updateCurrentUser.mockResolvedValue({ profile: { name: 'Rider', weeklyHours: 4 } })
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

  it('shows a captured illness and deletes it on Remove', async () => {
    render(
      <ReplyConfirmations
        msg={reply({ capturedStates: [{ id: 's1', label: 'Ill since 2026-10-12', removable: true }] })}
      />,
    )
    expect(screen.getByText('Ill since 2026-10-12')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(mocks.removeStateRemote).toHaveBeenCalledWith('test-token', 's1')
    expect(await screen.findByText('Removed.')).toBeInTheDocument()
  })

  it('keeps a captured state without calling the server, and says when Remove fails', async () => {
    mocks.removeStateRemote.mockRejectedValue(new Error('offline'))
    render(
      <ReplyConfirmations
        msg={reply({
          capturedStates: [
            { id: 's1', label: 'Ill since 2026-10-12', removable: true },
            { id: 's2', label: 'Less to give on 2026-10-13', removable: true },
          ],
        })}
      />,
    )

    await userEvent.click(screen.getAllByRole('button', { name: /keep/i })[0])
    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(screen.getByText('Kept.')).toBeInTheDocument()
    expect(mocks.removeStateRemote).toHaveBeenCalledWith('test-token', 's2')
    expect(await screen.findByText('Could not remove it. Try again later.')).toBeInTheDocument()
  })

  it('shows a pinned day off and lifts it on Remove (ai-trainer-ops#110)', async () => {
    render(
      <ReplyConfirmations
        msg={reply({
          capturedConstraints: [{ id: 'c1', label: 'No training on Saturday 2026-10-24', removable: true }],
        })}
      />,
    )
    expect(screen.getByText('No training on Saturday 2026-10-24')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(mocks.removeConstraintRemote).toHaveBeenCalledWith('test-token', 'c1')
    expect(await screen.findByText('Removed.')).toBeInTheDocument()
  })

  it('keeps a pinned long ride without calling the server, and says when Remove fails', async () => {
    mocks.removeConstraintRemote.mockRejectedValue(new Error('offline'))
    render(
      <ReplyConfirmations
        msg={reply({
          capturedConstraints: [
            { id: 'c1', label: 'Long ride on Sunday 2026-10-25, at least 120 min', removable: true },
            { id: 'c2', label: 'No training on Saturday 2026-10-24', removable: true },
          ],
        })}
      />,
    )

    await userEvent.click(screen.getAllByRole('button', { name: /keep/i })[0])
    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    expect(screen.getByText('Kept.')).toBeInTheDocument()
    expect(mocks.removeConstraintRemote).toHaveBeenCalledWith('test-token', 'c2')
    expect(await screen.findByText('Could not remove it. Try again later.')).toBeInTheDocument()
  })

  it('offers no Remove for "well again", which ends an illness', () => {
    render(
      <ReplyConfirmations
        msg={reply({
          capturedStates: [{ id: 's1', label: 'Well again: easy days until 2026-10-15', removable: false }],
        })}
      />,
    )
    expect(screen.getByText('Well again: easy days until 2026-10-15')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('proposes a profile change from chat and applies it only on Update', async () => {
    render(
      <ReplyConfirmations
        msg={reply({ profileProposal: { weeklyHours: 4, trainingDays: ['tuesday', 'saturday'], sports: null } })}
      />,
    )
    expect(screen.getByText('4 h a week; train on Tuesday, Saturday')).toBeInTheDocument()
    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /update profile/i }))

    expect(mocks.updateCurrentUser).toHaveBeenCalledWith('test-token', {
      weeklyHours: 4,
      trainingDays: ['tuesday', 'saturday'],
      sports: undefined,
    })
    expect(await screen.findByText(/Updated\./)).toBeInTheDocument()
    expect(useAppStore.getState().userProfile?.weeklyHours).toBe(4)
  })

  it('leaves the profile alone, and says when the update fails', async () => {
    mocks.updateCurrentUser.mockRejectedValueOnce(new Error('offline'))
    const { unmount } = render(<ReplyConfirmations msg={reply({ profileProposal: { sports: ['cycling', 'running'] } })} />)
    expect(screen.getByText('sports: Cycling, Running')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /leave it/i }))
    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
    expect(screen.getByText('Left as it was.')).toBeInTheDocument()
    unmount()

    render(<ReplyConfirmations msg={reply({ profileProposal: { sports: ['cycling', 'running'] } })} />)
    await userEvent.click(screen.getByRole('button', { name: /update profile/i }))
    expect(await screen.findByText('Could not update it. Try again later.')).toBeInTheDocument()
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
