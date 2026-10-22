import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DayActivities from './DayActivities'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, WorkoutFeedback } from '../store/useAppStore'

const { mockFetchWorkoutLogs, mockDelete, mockUpdate, mockRides, mockMetrics } = vi.hoisted(() => ({
  mockFetchWorkoutLogs: vi.fn(),
  mockDelete: vi.fn(),
  mockUpdate: vi.fn(),
  mockRides: vi.fn(),
  mockMetrics: vi.fn(),
}))

vi.mock('../services/user', () => ({
  fetchWorkoutLogs: mockFetchWorkoutLogs,
  deleteManualActivity: mockDelete,
  updateManualActivity: mockUpdate,
  addManualActivity: vi.fn(),
  fetchRideMetricsHistory: mockRides,
  fetchMetricsHistory: mockMetrics,
  uploadFitFiles: vi.fn(),
}))

const day = '2026-10-07'
const ride = (fields: Partial<RideMetricPoint>): RideMetricPoint => ({
  stravaActivityId: 1, activityDate: day, sportType: 'Ride', activityName: 'Morning Ride', durationSeconds: 5400, ...fields,
})

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockFetchWorkoutLogs.mockResolvedValue({})
  mockRides.mockResolvedValue([])
  mockMetrics.mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
  useAppStore.setState({ uiLanguage: 'en' })
})

describe('DayActivities (ai-trainer-ops#47, #48)', () => {
  it('lists what happened on the day, recorded or entered', () => {
    useAppStore.setState({
      rideMetricsHistory: [
        ride({}),
        ride({ stravaActivityId: 2, sportType: 'running', activitySource: 'logged', externalActivityId: `${day}#100`, durationSeconds: 1800 }),
        ride({ stravaActivityId: 3, activityDate: '2026-10-06' }),
      ],
    })
    render(<DayActivities date={day} />)

    expect(screen.getByText('Morning Ride')).toBeInTheDocument()
    expect(screen.getByText('1h 30m')).toBeInTheDocument()
    expect(screen.getByText('Entered by you')).toBeInTheDocument()
    expect(screen.getByText('30 min')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('shows one row per activity even when the list holds it twice', () => {
    useAppStore.setState({ rideMetricsHistory: [ride({ externalActivityId: 'i1' }), ride({ externalActivityId: 'i1' })] })
    render(<DayActivities date={day} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('leaves out a duration it does not know, and names an unnamed activity', () => {
    useAppStore.setState({ rideMetricsHistory: [ride({ activityName: null, durationSeconds: undefined })] })
    render(<DayActivities date={day} />)
    expect(screen.getByText('Activity')).toBeInTheDocument()
    expect(screen.queryByText(/min|h /)).not.toBeInTheDocument()
  })

  it('renders nothing for a day with nothing on it', () => {
    const { container } = render(<DayActivities date={day} />)
    expect(container).toBeEmptyDOMElement()
  })
})

// An activity entered with "Add activity": a logged row in the unplanned range.
const entered = (slot: number, fields: Partial<RideMetricPoint> = {}): RideMetricPoint =>
  ride({
    stravaActivityId: slot,
    sportType: 'running',
    activitySource: 'logged',
    externalActivityId: `${day}#${slot}`,
    durationSeconds: 3000,
    ...fields,
  })

const log = (fields: Partial<WorkoutFeedback> = {}): WorkoutFeedback => ({
  actualDurationMinutes: 50,
  perceivedEffort: 3,
  notes: '',
  completedAt: '',
  sport: 'running',
  ...fields,
})

describe('DayActivities shows and changes what was entered (ai-trainer-ops#207)', () => {
  it('shows effort, distance, heart rate, power and notes where they were entered', async () => {
    mockFetchWorkoutLogs.mockResolvedValue({
      [`${day}#100`]: log({
        sport: 'cycling',
        perceivedEffort: 4,
        distanceKm: 42.5,
        averageHeartRate: 151,
        normalizedPower: 230,
        notes: 'windy on the way back',
      }),
    })
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100, { sportType: 'cycling' })] })
    render(<DayActivities date={day} />)

    expect(await screen.findByText('Effort: 4/5 (Very Hard)')).toBeInTheDocument()
    expect(screen.getByText('Distance: 42.5 km')).toBeInTheDocument()
    expect(screen.getByText('Avg HR: 151 bpm')).toBeInTheDocument()
    expect(screen.getByText('NP: 230 W')).toBeInTheDocument()
    expect(screen.getByText('windy on the way back')).toBeInTheDocument()
  })

  it('leaves out what was not entered, and names a strength effort its own way', async () => {
    mockFetchWorkoutLogs.mockResolvedValue({ [`${day}#100`]: log({ sport: 'strength', perceivedEffort: 2 }) })
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100, { sportType: 'strength' })] })
    render(<DayActivities date={day} />)

    expect(await screen.findByText('Effort: 2/5 (Controlled)')).toBeInTheDocument()
    expect(screen.queryByText(/Distance|Avg HR|NP:/)).not.toBeInTheDocument()
  })

  it('shows the details in German', async () => {
    useAppStore.setState({ uiLanguage: 'de' })
    mockFetchWorkoutLogs.mockResolvedValue({ [`${day}#100`]: log({ distanceKm: 8 }) })
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100)] })
    render(<DayActivities date={day} />)

    expect(await screen.findByText('Distanz: 8 km')).toBeInTheDocument()
    expect(screen.getByText(/^Anstrengung: 3\/5/)).toBeInTheDocument()
  })

  it('reads no logs for a day with only recorded rides, and offers no edit for them', () => {
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [ride({})] })
    render(<DayActivities date={day} />)

    expect(mockFetchWorkoutLogs).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /Edit the/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete the/ })).not.toBeInTheDocument()
  })

  it('still lists the activity when its log cannot be read', async () => {
    mockFetchWorkoutLogs.mockRejectedValue(new Error('offline'))
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100)] })
    render(<DayActivities date={day} />)

    await waitFor(() => expect(mockFetchWorkoutLogs).toHaveBeenCalled())
    expect(screen.getByText('Entered by you')).toBeInTheDocument()
    expect(screen.queryByText(/Effort:/)).not.toBeInTheDocument()
  })

  it('edits the entry with the dashboard form, and shows the change', async () => {
    let saved = log({ notes: 'hills' })
    mockFetchWorkoutLogs.mockImplementation(async () => ({ [`${day}#100`]: saved }))
    mockUpdate.mockImplementation(async (_token: string, _date: string, _slot: number, fields: Partial<WorkoutFeedback>) => {
      saved = log({ ...fields })
    })
    // The dialog reloads the rides after a save; a new list is what makes the
    // day read the logs again.
    mockRides.mockImplementation(async () => [entered(100)])
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100)] })
    render(<DayActivities date={day} />)
    expect(await screen.findByText('hills')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: `Edit the running you entered on ${day}` }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Edit activity' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/Notes/)).toHaveValue('hills')

    await userEvent.clear(within(dialog).getByLabelText(/Notes/))
    await userEvent.type(within(dialog).getByLabelText(/Notes/), 'flat and fast')
    await userEvent.click(within(dialog).getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('tok', day, 100, expect.objectContaining({ notes: 'flat and fast' })))
    expect(await screen.findByText('flat and fast')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('deletes the entry after asking', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDelete.mockResolvedValue(undefined)
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100)] })
    const { container } = render(<DayActivities date={day} />)

    await userEvent.click(screen.getByRole('button', { name: `Delete the running you entered on ${day}` }))

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('tok', day, 100))
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('keeps the entry when the deletion is not confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    useAppStore.setState({ authToken: 'tok', rideMetricsHistory: [entered(100)] })
    render(<DayActivities date={day} />)

    await userEvent.click(screen.getByRole('button', { name: /Delete the running/ }))

    expect(mockDelete).not.toHaveBeenCalled()
    expect(screen.getByText('Entered by you')).toBeInTheDocument()
  })
})
