import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AddActivityDialog from './AddActivityDialog'
import { useAppStore } from '../store/useAppStore'
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
  render(<AddActivityDialog open onClose={onClose} />)
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

describe('AddActivityDialog (ai-trainer-ops#47)', () => {
  it('asks for five things and nothing a device would measure', () => {
    setup()
    expect(screen.getByLabelText('Day')).toHaveValue(today)
    expect(screen.getByRole('button', { name: /Run/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Duration (minutes)')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'How hard was it?' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Notes/)).toBeInTheDocument()
    expect(screen.queryByText(/Power/)).not.toBeInTheDocument()
  })

  it('cannot be saved without a duration', async () => {
    const { user } = setup()
    expect(screen.getByRole('button', { name: 'Save activity' })).toBeDisabled()
    await user.type(screen.getByLabelText('Duration (minutes)'), '0')
    expect(screen.getByRole('button', { name: 'Save activity' })).toBeDisabled()
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
    await user.click(screen.getByRole('button', { name: 'Save activity' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(mockAdd).toHaveBeenCalledWith('tok', {
      date: today,
      sport: 'running',
      durationMinutes: 45,
      perceivedEffort: 4,
      notes: 'hills',
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
    await user.click(screen.getByRole('button', { name: 'Save activity' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be in the future')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('falls back to a generic message for a non-Error failure', async () => {
    mockAdd.mockRejectedValueOnce('boom')
    const { user } = setup()
    await user.type(screen.getByLabelText('Duration (minutes)'), '30')
    await user.click(screen.getByRole('button', { name: 'Save activity' }))
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
    await user.click(screen.getByRole('button', { name: 'Save activity' }))
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

  it('corrects an entry in place: prefilled, the day fixed, no upload', async () => {
    mockUpdate.mockResolvedValue({ date: '2026-10-05', slot: 101, sport: 'running' })
    const onClose = vi.fn()
    render(
      <AddActivityDialog
        open
        onClose={onClose}
        editing={{ date: '2026-10-05', slot: 101, sport: 'running', durationMinutes: 40, perceivedEffort: 2, notes: 'easy' }}
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
    expect(mockUpdate).toHaveBeenCalledWith('tok', '2026-10-05', 101, {
      sport: 'running', durationMinutes: 55, perceivedEffort: 2, notes: 'easy',
    })
    expect(mockAdd).not.toHaveBeenCalled()
  })
})

