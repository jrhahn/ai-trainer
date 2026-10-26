import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render as renderDom, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import ColdStartNote, { COLD_START_ACTIVITIES, HISTORY_MONTHS } from './ColdStartNote'
import { IMPORT_POLL_MAX_FAILURES, IMPORT_POLL_MS } from '../hooks/useIntervalsImport'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, UserProfile } from '../store/useAppStore'
import { setProfile } from '../queries/profile'

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
  setProfile({
      name: 'Mara',
      trainingGoal: 'general_fitness',
      fitnessLevel: 'beginner',
      ...profile,
    } as UserProfile)
  useAppStore.setState({
    rideMetricsHistory: Array.from({ length: activities }, () => ({}) as RideMetricPoint),
  })
}

/** The answers the plan used, one chip each. */
const chips = () => screen.getAllByRole('listitem').map((li) => li.textContent)

const IDLE = { status: 'idle', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' }
/** backend/services/import_jobs.py, IMPORT_ERRORS["interrupted"]. */
const INTERRUPTED =
  'The import was interrupted, most likely by an update of the app. What arrived is kept; start the import again for the rest.'

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  // The note asks once whether an import is under way as it appears (ai-trainer-ops#284).
  mockProgress.mockReset()
  mockProgress.mockResolvedValue(IDLE)
})

afterEach(() => {
  vi.useRealTimers()
  // resetAll keeps the interface language, so the German test hands it back.
  useAppStore.setState({ uiLanguage: 'en' })
})

describe('ColdStartNote', () => {
  it('names no level the athlete never picked (ai-trainer-ops#214)', () => {
    setState({ fitnessLevel: null }, 0)
    render(<ColdStartNote />)

    expect(chips()).toEqual(['general fitness'])
  })

  it('names the answers the plan used and nothing it does not know (ai-trainer-ops#205)', () => {
    setState({ weeklyHours: 5, trainingDays: ['tuesday', 'saturday'] }, 0)
    render(<ColdStartNote />)

    expect(chips()).toEqual(['general fitness', '5 h a week', 'Tue, Sat', 'beginner level'])
    expect(screen.queryByText('Not known yet')).not.toBeInTheDocument()
    expect(screen.queryByText(/your FTP|training load|respond to sessions/)).not.toBeInTheDocument()
    // The sentence stands on its own now that the row before it is gone.
    expect(
      screen.getByText(
        'For the first week the coach keeps it moderate: mostly easy endurance, at most one harder session. Every activity you sync or log replaces a guess with a measurement.',
      ),
    ).toBeInTheDocument()
  })

  it('says the same in German, without a row of unknowns', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    setState({ weeklyHours: 5 }, 0)
    render(<ColdStartNote />)

    expect(screen.queryByText('Noch nicht bekannt')).not.toBeInTheDocument()
    // "Genutzt" read as a verb (ai-trainer-ops#223).
    expect(screen.getByText(/^Grundlage:/)).toBeInTheDocument()
    expect(screen.queryByText(/Genutzt/)).not.toBeInTheDocument()
    expect(screen.getByText(/^In der ersten Woche hält der Coach es moderat/)).toBeInTheDocument()
    expect(screen.queryByText(/^Deshalb/)).not.toBeInTheDocument()
  })

  it('names the race the plan works toward', () => {
    setState({ trainingGoal: 'race', raceDate: '2026-11-08' }, 0)
    render(<ColdStartNote />)

    const day = new Date(2026, 10, 8).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
    expect(chips()).toEqual([`a race on ${day}`, 'beginner level'])
  })

  it('says a race without a date when none was given', () => {
    setState({ trainingGoal: 'race' }, 0)
    render(<ColdStartNote />)

    expect(chips()).toEqual(['a race', 'beginner level'])
  })

  it('counts a stated FTP as known', () => {
    setState({ currentFTP: 240 }, 0)
    render(<ColdStartNote />)

    expect(chips()).toContain('FTP 240 W')
  })

  it('counts the first activities instead of promising a moderate week', () => {
    // The backend's moderate-week rule holds only while nothing is on record.
    setState({}, 3)
    render(<ColdStartNote />)

    expect(screen.getByRole('progressbar', { name: `3 of ${COLD_START_ACTIVITIES} activities` })).toHaveAttribute(
      'aria-valuenow',
      '3',
    )
    expect(screen.queryByText(/moderate/)).not.toBeInTheDocument()
  })

  it('stays away when the data failed to load', () => {
    setState({}, 0)
    useAppStore.setState({ dataLoadWarning: 'Some data failed to load (ride history). Refresh the page to retry.' })
    const { container } = render(<ColdStartNote />)

    expect(container).toBeEmptyDOMElement()
  })

  it('stays for a failed action, which leaves the ride history as it is (review of #1068)', () => {
    setState({}, 0)
    useAppStore.setState({ notice: 'The conversation could not be cleared. Try again.' })
    const { container } = render(<ColdStartNote />)

    expect(container).not.toBeEmptyDOMElement()
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
    expect(screen.getByText('Strava brings in your last 24 months by itself once connected.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /from intervals.icu/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Choose or drop/)).not.toBeInTheDocument()

    const upload = screen.getByRole('button', { name: 'Upload past rides' })
    await userEvent.click(upload)

    expect(upload).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/the gear menu, then “Export Original”/)).toBeInTheDocument()
    expect(screen.getByText(/Choose or drop .fit \/ .zip files/)).toBeInTheDocument()
  })

  it('offers intervals.icu alone while Strava is switched off (#991)', () => {
    setState({}, 0)
    useAppStore.setState({ stravaAvailable: false })
    render(<ColdStartNote />)

    expect(screen.getByRole('link', { name: 'Connect intervals.icu' })).toHaveAttribute(
      'href',
      '/settings#settings-activities',
    )
    expect(screen.queryByText(/Strava brings in/)).not.toBeInTheDocument()
    expect(screen.getByText(/rides that come only through Strava do not reach us/)).toBeInTheDocument()
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
      .mockResolvedValueOnce(IDLE) // the question as the note appears
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
    expect(mockProgress).toHaveBeenCalledTimes(3)
  })

  it('stays in view while the coach looks at a connect’s rides, and says so (ai-trainer-ops#306)', async () => {
    setState({}, COLD_START_ACTIVITIES + 4)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockProgress.mockResolvedValue({ ...IDLE, status: 'analysing', total: 12, processed: 12, imported: 12 })

    render(<ColdStartNote />)

    expect(await screen.findByText('Your history from intervals.icu')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('12 imported. Your coach is looking at them…')
    expect(screen.getByRole('button', { name: `Import my last ${HISTORY_MONTHS} months from intervals.icu` })).toBeDisabled()
  })

  it('says so when the import cannot start', async () => {
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockRejectedValue(new Error('intervals.icu is not connected'))
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('intervals.icu is not connected')
    // Only the question as the note appeared.
    expect(mockProgress).toHaveBeenCalledTimes(1)
  })

  it('reports an import that failed on the server', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress.mockResolvedValueOnce(IDLE).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({
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

  it('stops asking after a run of unanswered progress questions (review of #978)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress.mockRejectedValue(new Error('progress gone'))
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * (IMPORT_POLL_MAX_FAILURES + 3))
    })

    // The question as the note appeared went unanswered too, and changed nothing.
    expect(mockProgress).toHaveBeenCalledTimes(IMPORT_POLL_MAX_FAILURES + 1)
    expect(screen.getByRole('alert')).toHaveTextContent('The import stopped reporting its progress.')
    expect(screen.getByRole('button', { name: /from intervals.icu/ })).toBeEnabled()
  })

  it('stops asking once the note is gone', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setState({}, 0)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress
      .mockResolvedValueOnce(IDLE)
      .mockResolvedValue({ status: 'running', total: 5, processed: 1, imported: 1, skipped: 0, failedActivities: [], error: '' })
    const view = render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))
    view.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * 3)
    })

    // The question as the note appeared, and none after it went.
    expect(mockProgress).toHaveBeenCalledTimes(1)
  })
})

describe('ColdStartNote: an import a restart cut short (ai-trainer-ops#284)', () => {
  const connected = (activities: number) => {
    setState({}, activities)
    useAppStore.setState({ authToken: 'tok', intervalsConnection: { athleteId: 'i1' } as never })
  }
  const interrupted = (imported: number) => ({
    status: 'interrupted', total: 40, processed: 30, imported, skipped: 0, failedActivities: [], error: INTERRUPTED,
  })

  it('says so when the import it follows was cut short, and offers it again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    connected(0)
    mockTrigger.mockResolvedValue({ status: 'started' })
    mockProgress.mockResolvedValueOnce(IDLE).mockResolvedValue(interrupted(30))
    mockRides.mockResolvedValue(Array.from({ length: 30 }, () => ({}) as RideMetricPoint))
    mockMetrics.mockResolvedValue([])
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * 3)
    })

    expect(screen.getByRole('alert')).toHaveTextContent(INTERRUPTED)
    // What it stored is shown, and the card stays for the rest.
    await waitFor(() => expect(useAppStore.getState().rideMetricsHistory).toHaveLength(30))
    expect(screen.getByRole('heading', { name: 'Your history from intervals.icu' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /from intervals.icu/ })).toBeEnabled()
    expect(mockProgress).toHaveBeenCalledTimes(2)
  })

  it('shows an import cut short as the note appears, in German', async () => {
    connected(3)
    useAppStore.setState({ uiLanguage: 'de' })
    mockProgress.mockResolvedValue(interrupted(3))
    render(<ColdStartNote />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Der Import wurde unterbrochen, wahrscheinlich durch ein Update der App.',
    )
  })

  it('keeps an import cut short in view once its rides count, and starts it again', async () => {
    connected(12)
    mockProgress.mockResolvedValue(interrupted(12))
    mockTrigger.mockResolvedValue({ status: 'started' })
    render(<ColdStartNote />)

    expect(await screen.findByRole('alert')).toHaveTextContent(INTERRUPTED)
    expect(screen.queryByText('Your plan starts from your answers')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))

    expect(mockTrigger).toHaveBeenCalledWith('tok', HISTORY_MONTHS)
    expect(screen.getByRole('status')).toHaveTextContent('Importing…')
  })

  it('follows an import still running after a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    connected(12)
    mockProgress
      .mockResolvedValueOnce({ status: 'running', total: 40, processed: 5, imported: 0, skipped: 0, failedActivities: [], error: '' })
      .mockResolvedValue({ status: 'running', total: 40, processed: 9, imported: 0, skipped: 0, failedActivities: [], error: '' })
    render(<ColdStartNote />)

    expect(await screen.findByRole('status')).toHaveTextContent('Importing… 5 of 40 activities')
    expect(screen.getByRole('button', { name: /from intervals.icu/ })).toBeDisabled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS)
    })
    expect(screen.getByRole('status')).toHaveTextContent('Importing… 9 of 40 activities')
  })

  it('stops asking when the server knows of no import any more', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    connected(0)
    mockTrigger.mockResolvedValue({ status: 'started' })
    render(<ColdStartNote />)

    await userEvent.click(screen.getByRole('button', { name: /from intervals.icu/ }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * 4)
    })

    expect(screen.getByRole('alert')).toHaveTextContent('The import stopped reporting its progress.')
    expect(mockProgress).toHaveBeenCalledTimes(2)
  })

  it('asks nothing without an intervals.icu connection, and stays away once the rides count', () => {
    setState({}, 12)
    useAppStore.setState({ authToken: 'tok' })
    const { container } = render(<ColdStartNote />)

    expect(mockProgress).not.toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })
})
