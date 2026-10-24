import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TrainingAnswersSettings from './TrainingAnswersSettings'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const mocks = vi.hoisted(() => ({
  updateCurrentUser: vi.fn(),
  clearProfileRaceRemote: vi.fn(),
  fetchCurrentUser: vi.fn(),
  removeDayPreferenceRemote: vi.fn(),
}))
vi.mock('../services/user', () => mocks)

const PROFILE = {
  name: 'Mara',
  trainingGoal: 'general_fitness',
  fitnessLevel: 'intermediate',
  sports: ['cycling'],
  trainingDays: ['tuesday', 'thursday', 'saturday', 'sunday'],
} as UserProfile

function setProfile(overrides: Partial<UserProfile> = {}) {
  useAppStore.setState({ authToken: 'tok', userProfile: { ...PROFILE, ...overrides } })
}

/** The server answers with the profile as written. */
function echo() {
  mocks.updateCurrentUser.mockImplementation(async (_token: string, updates: Partial<UserProfile>) => ({
    profile: { ...useAppStore.getState().userProfile, ...updates },
  }))
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  echo()
  mocks.clearProfileRaceRemote.mockResolvedValue(undefined)
})

afterEach(() => {
  useAppStore.setState({ uiLanguage: 'en' })
})

describe('TrainingAnswersSettings (ai-trainer-ops#202)', () => {
  it('shows the onboarding answers as they are stored', () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    expect(screen.getByRole('button', { name: 'Cycling' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Running' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Tue' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Mon' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'No race' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Intermediate' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('selects no level the athlete never picked (ai-trainer-ops#214)', () => {
    setProfile({ fitnessLevel: null })
    render(<TrainingAnswersSettings />)

    for (const name of ['Beginner', 'Intermediate', 'Advanced']) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'false')
    }
  })

  it('adds a sport and saves it with the profile write the chat offer makes', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Running' }))

    expect(mocks.updateCurrentUser).toHaveBeenCalledWith('tok', { sports: ['cycling', 'running'] })
    await waitFor(() => expect(useAppStore.getState().userProfile?.sports).toEqual(['cycling', 'running']))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('keeps at least one sport', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Cycling' }))

    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Keep at least one sport.')
  })

  it('changes the training days, in the order of the week', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Mon' }))

    expect(mocks.updateCurrentUser).toHaveBeenCalledWith('tok', {
      trainingDays: ['monday', 'tuesday', 'thursday', 'saturday', 'sunday'],
    })
  })

  it('keeps at least one training day', async () => {
    setProfile({ trainingDays: ['tuesday'] })
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Tue' }))

    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Keep at least one training day.')
  })

  it('sets a race once it has a date, and its description on leaving the field', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Training for a race' }))
    // Nothing is saved before there is a date.
    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Race Date'), { target: { value: '2027-06-12' } })
    expect(mocks.updateCurrentUser).toHaveBeenCalledWith('tok', {
      trainingGoal: 'race',
      raceDate: '2027-06-12',
      raceDescription: undefined,
    })
    await waitFor(() => expect(useAppStore.getState().userProfile?.raceDate).toBe('2027-06-12'))

    await userEvent.type(screen.getByLabelText('Race Description'), 'Gran fondo, 120 km')
    await userEvent.tab()
    expect(mocks.updateCurrentUser).toHaveBeenLastCalledWith('tok', {
      trainingGoal: 'race',
      raceDate: '2027-06-12',
      raceDescription: 'Gran fondo, 120 km',
    })
  })

  it('keeps a description typed while the date was still saving', async () => {
    setProfile()
    let finish: (value: unknown) => void = () => {}
    mocks.updateCurrentUser.mockImplementationOnce(
      (_token: string, updates: Partial<UserProfile>) =>
        new Promise((resolve) => {
          finish = () => resolve({ profile: { ...useAppStore.getState().userProfile, ...updates } })
        }),
    )
    render(<TrainingAnswersSettings />)
    await userEvent.click(screen.getByRole('button', { name: 'Training for a race' }))

    fireEvent.change(screen.getByLabelText('Race Date'), { target: { value: '2027-06-12' } })
    fireEvent.change(screen.getByLabelText('Race Description'), { target: { value: 'Gran fondo' } })
    finish(undefined)
    await waitFor(() => expect(useAppStore.getState().userProfile?.raceDate).toBe('2027-06-12'))

    expect(screen.getByLabelText('Race Description')).toHaveValue('Gran fondo')
    fireEvent.blur(screen.getByLabelText('Race Description'))
    expect(mocks.updateCurrentUser).toHaveBeenLastCalledWith('tok', {
      trainingGoal: 'race',
      raceDate: '2027-06-12',
      raceDescription: 'Gran fondo',
    })
  })

  it('does not save a description that did not change', async () => {
    setProfile({ trainingGoal: 'race', raceDate: '2027-06-12', raceDescription: 'Gran fondo' })
    render(<TrainingAnswersSettings />)

    screen.getByLabelText('Race Description').focus()
    await userEvent.tab()

    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
  })

  it('asks for a date when the race date is emptied', () => {
    setProfile({ trainingGoal: 'race', raceDate: '2027-06-12' })
    render(<TrainingAnswersSettings />)

    fireEvent.change(screen.getByLabelText('Race Date'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Race Description'), { target: { value: 'x' } })
    fireEvent.blur(screen.getByLabelText('Race Description'))

    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Enter the race date.')
  })

  it('clears the race the way the chat\'s "no race" does', async () => {
    setProfile({ trainingGoal: 'race', raceDate: '2027-06-12', raceDescription: 'Gran fondo' })
    mocks.fetchCurrentUser.mockResolvedValue({ profile: { ...PROFILE, trainingGoal: 'general_fitness' } })
    render(<TrainingAnswersSettings />)
    expect(screen.getByLabelText('Race Date')).toHaveValue('2027-06-12')

    await userEvent.click(screen.getByRole('button', { name: 'No race' }))

    expect(mocks.clearProfileRaceRemote).toHaveBeenCalledWith('tok')
    await waitFor(() => expect(useAppStore.getState().userProfile?.trainingGoal).toBe('general_fitness'))
    expect(screen.queryByLabelText('Race Date')).not.toBeInTheDocument()
  })

  it('closes an unsaved race without a call', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Training for a race' }))
    await userEvent.click(screen.getByRole('button', { name: 'No race' }))

    expect(mocks.clearProfileRaceRemote).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Race Date')).not.toBeInTheDocument()
  })

  it('changes the level, and does nothing for the level already set', async () => {
    setProfile()
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Intermediate' }))
    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Beginner' }))
    expect(mocks.updateCurrentUser).toHaveBeenCalledWith('tok', { fitnessLevel: 'beginner' })
  })

  it('says so when a save fails', async () => {
    setProfile()
    mocks.updateCurrentUser.mockRejectedValueOnce(new Error('Could not save. Please try again.'))
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Advanced' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save. Please try again.')
  })

  it('asks to sign in again without a session', async () => {
    useAppStore.setState({ authToken: null, userProfile: PROFILE })
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Advanced' }))

    expect(mocks.updateCurrentUser).not.toHaveBeenCalled()
    expect(await screen.findByRole('alert')).toHaveTextContent('Please sign in again.')
  })

  it('reads a profile without sports as cycling', () => {
    setProfile({ sports: undefined, trainingDays: undefined })
    render(<TrainingAnswersSettings />)

    expect(screen.getByRole('button', { name: 'Cycling' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Mon' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('lists the preferred days the chat kept, and drops one (ai-trainer-ops#204)', async () => {
    setProfile({ dayPreferences: [{ prefer: 'monday', over: 'tuesday' }] })
    mocks.removeDayPreferenceRemote.mockResolvedValue(undefined)
    mocks.fetchCurrentUser.mockResolvedValue({ profile: { ...PROFILE, dayPreferences: [] } })
    render(<TrainingAnswersSettings />)

    expect(screen.getByText('Monday rather than Tuesday')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Monday rather than Tuesday' }))

    expect(mocks.removeDayPreferenceRemote).toHaveBeenCalledWith('tok', 'monday', 'tuesday')
    await waitFor(() => expect(screen.queryByText('Monday rather than Tuesday')).not.toBeInTheDocument())
  })

  it('says so when a preference cannot be dropped', async () => {
    setProfile({ dayPreferences: [{ prefer: 'monday', over: 'tuesday' }] })
    mocks.removeDayPreferenceRemote.mockRejectedValue(new Error('Could not save. Please try again.'))
    render(<TrainingAnswersSettings />)

    await userEvent.click(screen.getByRole('button', { name: 'Remove Monday rather than Tuesday' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save. Please try again.')
    expect(screen.getByText('Monday rather than Tuesday')).toBeInTheDocument()
  })

  it('shows no preferred days when there are none', () => {
    setProfile({ dayPreferences: [] })
    render(<TrainingAnswersSettings />)
    expect(screen.queryByText('Preferred days, from the chat:')).not.toBeInTheDocument()
  })

  it('renders nothing without a profile', () => {
    const { container } = render(<TrainingAnswersSettings />)
    expect(container).toBeEmptyDOMElement()
  })

  it('speaks German', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setProfile()
    render(<TrainingAnswersSettings />)

    expect(screen.getByRole('heading', { name: 'Dein Training' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kein Rennen' })).toBeInTheDocument()
  })
})
