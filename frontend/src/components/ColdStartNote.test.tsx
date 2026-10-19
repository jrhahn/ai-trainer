import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render as renderDom, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import ColdStartNote, { COLD_START_ACTIVITIES, HISTORY_MONTHS, IMPORT_POLL_MS } from './ColdStartNote'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, UserProfile } from '../store/useAppStore'

const { mockTrigger, mockProgress, mockRides, mockMetrics } = vi.hoisted(() => ({
  mockTrigger: vi.fn(),
  mockProgress: vi.fn(),
  mockRides: vi.fn(),
  mockMetrics: vi.fn(),
}))

vi.mock('../services/intervals', () => ({
  triggerIntervalsHistoryImport: mockTrigger,
  getIntervalsImportProgress: mockProgress,
}))
vi.mock('../services/user', () => ({
  fetchRideMetricsHistory: mockRides,
  fetchMetricsHistory: mockMetrics,
  uploadFitFiles: vi.fn(),
}))

// The note links to Settings now (ai-trainer-ops#157), so it needs a router.
function render(ui: ReactElement) {
  return renderDom(<MemoryRouter>{ui}</MemoryRouter>)
}

function setState(profile: Partial<UserProfile>, activities: number) {
  useAppStore.setState({
    userProfile: {
      name: 'Mara',
      trainingGoal: 'general_fitness',
      fitnessLevel: 'beginner',
      ...profile,
    } as UserProfile,
    rideMetricsHistory: Array.from({ length: activities }, () => ({}) as RideMetricPoint),
  })
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ColdStartNote', () => {
  it('names the answers the plan used and what it does not know', () => {
    setState({ weeklyHours: 5, trainingDays: ['tuesday', 'saturday'] }, 0)
    render(<ColdStartNote />)

    expect(screen.getByText('It used: general fitness, 5 h a week, Tue, Sat, beginner level.')).toBeInTheDocument()
    expect(
      screen.getByText('It does not know yet: your FTP, your recent training load, how you respond to sessions.')
    ).toBeInTheDocument()
    expect(screen.getByText(/keep the first week moderate/)).toBeInTheDocument()
  })

  it('names the race the plan works toward', () => {
    setState({ trainingGoal: 'race', raceDate: '2026-11-08' }, 0)
    render(<ColdStartNote />)

    const day = new Date(2026, 10, 8).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
    expect(screen.getByText(`It used: a race on ${day}, beginner level.`)).toBeInTheDocument()
  })

  it('says a race without a date when none was given', () => {
    setState({ trainingGoal: 'race' }, 0)
    render(<ColdStartNote />)

    expect(screen.getByText('It used: a race, beginner level.')).toBeInTheDocument()
  })

  it('counts a stated FTP as known', () => {
    setState({ currentFTP: 240 }, 0)
    render(<ColdStartNote />)

    expect(screen.getByText(/FTP 240 W/)).toBeInTheDocument()
    expect(screen.getByText(/It does not know yet: your recent training load/)).toBeInTheDocument()
  })

  it('counts the first activities instead of promising a moderate week', () => {
    // The backend's moderate-week rule holds only while nothing is on record.
    setState({}, 3)
    render(<ColdStartNote />)

    expect(screen.getByText(`3 of ${COLD_START_ACTIVITIES} activities so far. Every one replaces a guess with a measurement.`)).toBeInTheDocument()
    expect(screen.queryByText(/moderate/)).not.toBeInTheDocument()
  })

  it('stays away when the data failed to load', () => {
    setState({}, 0)
    useAppStore.setState({ dataLoadWarning: 'Some data failed to load (ride history). Refresh the page to retry.' })
    const { container } = render(<ColdStartNote />)

    expect(container).toBeEmptyDOMElement()
  })

  it('goes away once enough activities are synced', () => {
    setState({}, COLD_START_ACTIVITIES)
    const { container } = render(<ColdStartNote />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe('ColdStartNote: bringing a history in (ai-trainer-ops#157)', () => {
  it('offers to connect a service and to upload when nothing is connected', async () => {
    setState({}, 0)
    render(<ColdStartNote />)

    expect(screen.getByRole('link', { name: 'Connect intervals.icu or Strava' })).toHaveAttribute(
      'href',
      '/settings#settings-activities',
    )
    expect(screen.queryByRole('button', { name: /from intervals.icu/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Choose or drop/)).not.toBeInTheDocument()

    const upload = screen.getByRole('button', { name: 'Upload past rides' })
    await userEvent.click(upload)

    expect(upload).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/the gear menu, then “Export Original”/)).toBeInTheDocument()
    expect(screen.getByText(/Choose or drop .fit \/ .zip files/)).toBeInTheDocument()
  })

  it('does not ask a Strava athlete to connect again', () => {
    setState({}, 0)
    useAppStore.setState({ stravaConnection: { athleteId: 1 } as never })
    render(<ColdStartNote />)

    expect(screen.queryByRole('link', { name: 'Connect intervals.icu or Strava' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload past rides' })).toBeInTheDocument()
  })

  it('imports the intervals.icu history with progress and then shows the rides', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress
      .mockResolvedValueOnce({ status: 'running', total: 40, processed: 10, imported: 10, skipped: 0, failedActivities: [], error: '' })
      .mockResolvedValue({ status: 'done', total: 40, processed: 40, imported: 38, skipped: 2, failedActivities: [], error: '' })
    const rides = Array.from({ length: 38 }, () => ({}) as RideMetricPoint)
    mockRides.mockResolvedValue(rides)
    mockMetrics.mockResolvedValue([])
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: `Import my last ${HISTORY_MONTHS} months from intervals.icu` }))

    expect(mockTrigger).toHaveBeenCalledWith('tok', HISTORY_MONTHS)
    expect(screen.getByRole('status')).toHaveTextContent('Importing…')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS)
    })
    expect(screen.getByRole('status')).toHaveTextContent('Importing… 10 of 40 activities')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS)
    })
    await waitFor(() => expect(useAppStore.getState().rideMetricsHistory).toHaveLength(38))
    // Eight or more activities: the note has done its job and goes.
    expect(screen.queryByText('Your plan starts from your answers')).not.toBeInTheDocument()
    expect(mockProgress).toHaveBeenCalledTimes(2)
  })

  it('says so when the import cannot start', async () => {
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockRejectedValue(new Error('intervals.icu is not connected'))
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('intervals.icu is not connected')
    expect(mockProgress).not.toHaveBeenCalled()
  })

  it('reports an import that failed on the server', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({
      status: 'error', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: 'intervals.icu refused the key',
    })
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * 2)
    })

    expect(await screen.findByRole('alert')).toHaveTextContent('intervals.icu refused the key')
    expect(mockRides).not.toHaveBeenCalled()
  })
})
