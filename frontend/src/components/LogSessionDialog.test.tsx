import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LogSessionDialog from './LogSessionDialog'
import { useAppStore, type TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

const { mockAdd, mockUpdate, mockRides, mockMetrics } = vi.hoisted(() => ({
  mockAdd: vi.fn(),
  mockUpdate: vi.fn(),
  mockRides: vi.fn(),
  mockMetrics: vi.fn(),
}))

vi.mock('../services/user', () => ({
  uploadFitFiles: vi.fn(),
  addManualActivity: mockAdd,
  updateManualActivity: mockUpdate,
  fetchRideMetricsHistory: mockRides,
  fetchMetricsHistory: mockMetrics,
}))

const today = formatLocalDate(new Date())

function setup(onClose = vi.fn()) {
  render(<LogSessionDialog open onClose={onClose} />)
  return { onClose, user: userEvent.setup() }
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok' })
  vi.clearAllMocks()
  mockAdd.mockResolvedValue({ date: today, slot: 100, sport: 'running' })
  mockRides.mockResolvedValue([{ stravaActivityId: 1, activityDate: today, sportType: 'running' }])
  mockMetrics.mockResolvedValue([])
})

describe('LogSessionDialog: an entry of its own (ai-trainer-ops#47)', () => {
  it('asks for the day, sport, minutes, effort and notes, and for a ride the optional numbers', () => {
    setup()
    expect(screen.getByRole('heading', { name: 'Log a session' })).toBeInTheDocument()
    expect(screen.getByLabelText('Day')).toHaveValue(today)
    expect(screen.getByRole('button', { name: /Run/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Duration (minutes)')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'How hard was it?' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Notes/)).toBeInTheDocument()
    // Optional per sport (ai-trainer-ops#169): a ride by default.
    expect(screen.getByLabelText(/Normalized power/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Avg heart rate/)).toBeInTheDocument()
    // No plan question without a way to log a planned session.
    expect(screen.queryByText('Was this a planned session?')).not.toBeInTheDocument()
  })

  it('cannot be saved without a duration', async () => {
    const { user } = setup()
    expect(screen.getByRole('button', { name: 'Save session' })).toBeDisabled()
    await user.type(screen.getByLabelText('Duration (minutes)'), '0')
    expect(screen.getByRole('button', { name: 'Save session' })).toBeDisabled()
  })

  it('does not offer a day in the future', () => {
    setup()
    expect(screen.getByLabelText('Day')).toHaveAttribute('max', today)
  })

  it('saves the entry, refreshes activities and load, and closes', async () => {
    const { onClose, user } = setup()
    await user.click(screen.getByRole('button', { name: /Run/ }))
    await user.type(screen.getByLabelText('Duration (minutes)'), '45')
    await user.click(screen.getByRole('button', { name: /^4 of 5/ }))
    await user.type(screen.getByLabelText(/Notes/), '  hills  ')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(mockAdd).toHaveBeenCalledWith('tok', {
      date: today,
      sport: 'running',
      durationMinutes: 45,
      perceivedEffort: 4,
      notes: 'hills',
      // Explicitly null rather than omitted: the athlete left it blank for a
      // sport that *has* a distance, which is "none", not "say nothing"
      // (ai-trainer-ops#47).
      distanceKm: null,
      averageHeartRate: null,
    })
    expect(useAppStore.getState().rideMetricsHistory).toHaveLength(1)
    expect(mockMetrics).toHaveBeenCalled()
  })

  it('names the effort the way a lifter does for strength', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('button', { name: /Strength/ }))
    expect(screen.getByRole('button', { name: '2 of 5 — Controlled' })).toBeInTheDocument()
  })

  it('says why when saving fails, and stays open', async () => {
    mockAdd.mockRejectedValueOnce(new Error('An activity cannot be in the future.'))
    const { onClose, user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be in the future')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('falls back to a generic message for a non-Error failure', async () => {
    mockAdd.mockRejectedValueOnce('boom')
    const { user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the activity.')
  })

  it('cancel closes without saving', async () => {
    const { onClose, user } = setup()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(mockAdd).not.toHaveBeenCalled()
  })

  it('does nothing without a session', async () => {
    useAppStore.setState({ authToken: null })
    const { user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))
    expect(mockAdd).not.toHaveBeenCalled()
  })

  it('offers the file upload as the other way in (ai-trainer-ops#48)', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('tab', { name: 'Upload a file' }))

    expect(screen.getByText('Choose or drop .fit / .zip files')).toBeInTheDocument()
    expect(screen.queryByLabelText('Duration (minutes)')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Enter it' }))
    expect(screen.getByLabelText('Duration (minutes)')).toBeInTheDocument()
  })

  it('offers no distance for a strength session, and sends no key for one', async () => {
    // A gym session has no distance, and offering the field invites a number
    // that means nothing — the strength model reads sets, reps and load (#714).
    // Omitting the key rather than sending null also means a sport correction
    // cannot blank a distance the entry still has (ai-trainer-ops#47).
    mockAdd.mockResolvedValue({ date: today, slot: 100, sport: 'strength' })
    render(<LogSessionDialog open onClose={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Strength/ }))

    expect(screen.queryByLabelText(/Distance/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/heart rate/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Normalized power/)).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Duration (minutes)'), '50')
    await user.click(screen.getByRole('button', { name: /^3 of 5/ }))
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(mockAdd.mock.calls[0][1]).not.toHaveProperty('distanceKm')
    expect(mockAdd.mock.calls[0][1]).not.toHaveProperty('averageHeartRate')
    expect(mockAdd.mock.calls[0][1]).not.toHaveProperty('normalizedPower')
  })

  it('sends the distance the athlete typed for a run', async () => {
    mockAdd.mockResolvedValue({ date: today, slot: 100, sport: 'running' })
    render(<LogSessionDialog open onClose={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Run/ }))
    await user.type(screen.getByLabelText('Duration (minutes)'), '45')
    await user.type(screen.getByLabelText(/Distance/), '10.5')
    await user.click(screen.getByRole('button', { name: /^3 of 5/ }))
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(mockAdd.mock.calls[0][1]).toMatchObject({ distanceKm: 10.5 })
  })

  it('corrects an entry in place: prefilled, the day fixed, no upload', async () => {
    mockUpdate.mockResolvedValue({ date: '2026-10-05', slot: 101, sport: 'running' })
    const onClose = vi.fn()
    render(
      <LogSessionDialog
        open
        onClose={onClose}
        editing={{
          date: '2026-10-05',
          slot: 101,
          sport: 'running',
          durationMinutes: 40,
          perceivedEffort: 2,
          distanceKm: '8.2',
          averageHeartRate: '150',
          notes: 'easy',
        }}
      />
    )
    const user = userEvent.setup()

    expect(screen.getByRole('heading', { name: 'Edit activity' })).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Day')).toBeDisabled()
    expect(screen.getByLabelText('Duration (minutes)')).toHaveValue(40)
    expect(screen.getByRole('button', { name: /^2 of 5/ })).toHaveAttribute('aria-pressed', 'true')

    await user.clear(screen.getByLabelText('Duration (minutes)'))
    await user.type(screen.getByLabelText('Duration (minutes)'), '55')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    // The prefilled distance comes back unchanged, which is what makes a
    // correction to the duration leave the distance alone.
    expect(screen.getByLabelText(/Distance/)).toHaveValue(8.2)
    expect(mockUpdate).toHaveBeenCalledWith('tok', '2026-10-05', 101, {
      sport: 'running',
      durationMinutes: 55,
      perceivedEffort: 2,
      notes: 'easy',
      distanceKm: 8.2,
      averageHeartRate: 150,
    })
    expect(mockAdd).not.toHaveBeenCalled()
  })
})

describe('LogSessionDialog: optional numbers per sport (ai-trainer-ops#169)', () => {
  it('sends a ride\'s normalized power and heart rate', async () => {
    mockAdd.mockResolvedValue({ date: today, slot: 100, sport: 'cycling' })
    const { user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '60')
    await user.type(screen.getByLabelText(/Distance/), '30')
    await user.type(screen.getByLabelText(/Normalized power/), '200')
    await user.type(screen.getByLabelText(/Avg heart rate/), '142')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(mockAdd.mock.calls[0][1]).toMatchObject({
      sport: 'cycling',
      durationMinutes: 60,
      distanceKm: 30,
      normalizedPower: 200,
      averageHeartRate: 142,
    })
  })

  it('takes a run\'s heart rate and offers no power', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('button', { name: /Run/ }))
    expect(screen.queryByLabelText(/Normalized power/)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Duration (minutes)'), '40')
    await user.type(screen.getByLabelText(/Avg heart rate/), '155')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(mockAdd.mock.calls[0][1]).toMatchObject({ sport: 'running', averageHeartRate: 155 })
    expect(mockAdd.mock.calls[0][1]).not.toHaveProperty('normalizedPower')
  })

  it("refuses what the server refuses: the bounds of each number", async () => {
    const { user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '60')
    const save = screen.getByRole('button', { name: 'Save session' })

    await user.type(screen.getByLabelText(/Avg heart rate/), '20')
    expect(save).toBeDisabled()
    await user.clear(screen.getByLabelText(/Avg heart rate/))
    await user.type(screen.getByLabelText(/Normalized power/), '2600')
    expect(save).toBeDisabled()
    await user.clear(screen.getByLabelText(/Normalized power/))
    expect(save).toBeEnabled()

    expect(screen.getByLabelText('Duration (minutes)')).toHaveAttribute('max', '1440')
    expect(screen.getByLabelText(/Normalized power/)).toHaveAttribute('max', '2500')
    expect(screen.getByLabelText(/Avg heart rate/)).toHaveAttribute('min', '30')
    expect(screen.getByLabelText(/Notes/)).toHaveAttribute('maxLength', '2000')
  })
})

const ride: TrainingDay = {
  date: today,
  workoutType: 'endurance',
  title: 'Base spin',
  description: 'Zone 2',
  durationMinutes: 60,
}

describe('LogSessionDialog: the planned session (ai-trainer-ops#170, #171)', () => {
  function withPlan(plan: TrainingDay[]) {
    useAppStore.setState({ trainingPlan: plan, rideMetricsHistory: [] })
  }

  it('asks whether it was the planned session, and logs it as that one', async () => {
    withPlan([ride])
    const onLogPlanned = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    render(<LogSessionDialog open onClose={onClose} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    expect(screen.getByText('Was this a planned session?')).toBeInTheDocument()
    // The one open ride of the day is the answer until the athlete says otherwise.
    expect(screen.getByRole('radio', { name: 'Yes: Base spin (60 min)' })).toBeChecked()

    await user.type(screen.getByLabelText('Duration (minutes)'), '60')
    await user.type(screen.getByLabelText(/Normalized power/), '200')
    await user.type(screen.getByLabelText(/Distance/), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(mockAdd).not.toHaveBeenCalled()
    expect(onLogPlanned).toHaveBeenCalledWith(
      ride,
      expect.objectContaining({
        actualDurationMinutes: 60,
        perceivedEffort: 3,
        normalizedPower: 200,
        distanceKm: 30,
      })
    )
    expect(onLogPlanned.mock.calls[0][1].completedAt).toBeTruthy()
  })

  it('becomes a session of its own when the athlete says no', async () => {
    withPlan([ride])
    const onLogPlanned = vi.fn()
    render(<LogSessionDialog open onClose={vi.fn()} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'No, an extra session' }))
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(onLogPlanned).not.toHaveBeenCalled()
  })

  it('does not take a run for the planned ride', async () => {
    withPlan([ride])
    const onLogPlanned = vi.fn()
    render(<LogSessionDialog open onClose={vi.fn()} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Run/ }))
    expect(screen.getByRole('radio', { name: 'No, an extra session' })).toBeChecked()
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    await waitFor(() => expect(mockAdd).toHaveBeenCalled())
    expect(onLogPlanned).not.toHaveBeenCalled()
  })

  it('takes the sport of the session the athlete picks', async () => {
    const gym: TrainingDay = { ...ride, slot: 1, workoutType: 'strength', title: 'Gym' }
    withPlan([ride, gym])
    render(<LogSessionDialog open onClose={vi.fn()} onLogPlanned={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('radio', { name: 'Yes: Gym (60 min)' }))
    expect(screen.getByRole('button', { name: /Strength/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByLabelText(/Distance/)).not.toBeInTheDocument()
  })

  it('asks nothing about a rest day or a session already done', () => {
    const rest: TrainingDay = { ...ride, workoutType: 'rest', title: 'Rest' }
    withPlan([{ ...ride, completed: true }])
    const { unmount } = render(<LogSessionDialog open onClose={vi.fn()} onLogPlanned={vi.fn()} />)
    expect(screen.queryByText('Was this a planned session?')).not.toBeInTheDocument()
    unmount()

    withPlan([rest])
    render(<LogSessionDialog open onClose={vi.fn()} onLogPlanned={vi.fn()} />)
    expect(screen.queryByText('Was this a planned session?')).not.toBeInTheDocument()
  })

  it('opened from the card: that session, its day and sport fixed, the plan prefilled', async () => {
    const onLogPlanned = vi.fn().mockResolvedValue(undefined)
    render(<LogSessionDialog open onClose={vi.fn()} session={ride} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.queryByText('Was this a planned session?')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Day')).toBeDisabled()
    expect(screen.getByLabelText('Duration (minutes)')).toHaveValue(60)
    expect(screen.getByRole('button', { name: /Run/ })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Save session' }))
    await waitFor(() => expect(onLogPlanned).toHaveBeenCalledTimes(1))
    expect(onLogPlanned.mock.calls[0][0]).toBe(ride)
  })

  it('says when the save failed, keeps the answers, and saves again (ai-trainer-ops#127)', async () => {
    const onLogPlanned = vi
      .fn()
      .mockRejectedValueOnce(new Error('Request failed'))
      .mockResolvedValueOnce(undefined)
    const onClose = vi.fn()
    render(<LogSessionDialog open onClose={onClose} session={ride} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Save session' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The session was not saved.')
    expect(alert).toHaveTextContent('Request failed')
    expect(screen.getByLabelText('Duration (minutes)')).toHaveValue(60)

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(onLogPlanned).toHaveBeenCalledTimes(2)
  })

  it('takes one save at a time', async () => {
    let finish: () => void = () => {}
    const onLogPlanned = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    render(<LogSessionDialog open onClose={vi.fn()} session={ride} onLogPlanned={onLogPlanned} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Save session' }))
    const saving = screen.getByRole('button', { name: /saving/i })
    expect(saving).toBeDisabled()
    await user.click(saving)
    expect(onLogPlanned).toHaveBeenCalledTimes(1)
    finish()
  })

  it('words the effort scale for a lifter on a planned strength session', () => {
    render(
      <LogSessionDialog
        open
        onClose={vi.fn()}
        session={{ ...ride, workoutType: 'strength', title: 'Gym' }}
        onLogPlanned={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: '2 of 5 — Controlled' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Exercises, sets, load/)).toBeInTheDocument()
  })
})
