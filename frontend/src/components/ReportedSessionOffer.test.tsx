import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ReportedSessionOffer from './ReportedSessionOffer'
import { useAppStore, type TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

const { mockAdd, mockSaveLog, mockRides, mockMetrics } = vi.hoisted(() => ({
  mockAdd: vi.fn(),
  mockSaveLog: vi.fn(),
  mockRides: vi.fn(),
  mockMetrics: vi.fn(),
}))

vi.mock('../services/user', () => ({
  uploadFitFiles: vi.fn(),
  addManualActivity: mockAdd,
  updateManualActivity: vi.fn(),
  saveWorkoutLog: mockSaveLog,
  fetchRideMetricsHistory: mockRides,
  fetchMetricsHistory: mockMetrics,
  fetchTrainingPlan: vi.fn().mockResolvedValue([]),
  saveTrainingPlan: vi.fn(),
}))

const today = formatLocalDate(new Date())
const reported = {
  date: today,
  sport: 'cycling' as const,
  durationMinutes: 60,
  distanceKm: 30,
  normalizedPower: 200,
}

function renderOffer() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReportedSessionOffer session={reported} />
    </QueryClientProvider>
  )
  return userEvent.setup()
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok', trainingPlan: [] })
  vi.clearAllMocks()
  mockAdd.mockResolvedValue({ date: today, slot: 100, sport: 'cycling' })
  mockSaveLog.mockResolvedValue(undefined)
  mockRides.mockResolvedValue([])
  mockMetrics.mockResolvedValue([])
})

describe('ReportedSessionOffer (ai-trainer-ops#166)', () => {
  it('asks, and says what it would enter', () => {
    renderOffer()
    expect(screen.getByText('Shall I enter this as a session?')).toBeInTheDocument()
    expect(screen.getByText('Ride · 60 min · 30 km · 200 W NP')).toBeInTheDocument()
  })

  it('opens the form prefilled, and says so once the athlete saved it', async () => {
    const user = renderOffer()
    await user.click(screen.getByRole('button', { name: 'Enter it' }))

    expect(screen.getByRole('heading', { name: 'Log a session' })).toBeInTheDocument()
    expect(screen.getByLabelText('Duration (minutes)')).toHaveValue(60)
    expect(screen.getByLabelText(/Distance/)).toHaveValue(30)
    expect(screen.getByLabelText(/Normalized power/)).toHaveValue(200)
    expect(screen.getByRole('button', { name: /Ride/ })).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: '4 of 5 — Very Hard' }))
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    expect(await screen.findByText('Entered.')).toBeInTheDocument()
    expect(mockAdd).toHaveBeenCalledWith('tok', expect.objectContaining({
      date: today,
      sport: 'cycling',
      durationMinutes: 60,
      distanceKm: 30,
      normalizedPower: 200,
      perceivedEffort: 4,
    }))
  })

  it('logs it as the planned session when that is the answer', async () => {
    const spin: TrainingDay = {
      date: today,
      workoutType: 'endurance',
      title: 'Base spin',
      description: '',
      durationMinutes: 60,
    }
    useAppStore.setState({ trainingPlan: [spin] })
    const user = renderOffer()
    await user.click(screen.getByRole('button', { name: 'Enter it' }))

    expect(screen.getByRole('radio', { name: 'Yes: Base spin (60 min)' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save session' }))

    expect(await screen.findByText('Entered.')).toBeInTheDocument()
    expect(mockAdd).not.toHaveBeenCalled()
    expect(mockSaveLog).toHaveBeenCalledWith(
      'tok',
      today,
      expect.objectContaining({ actualDurationMinutes: 60, normalizedPower: 200, distanceKm: 30 }),
      0
    )
  })

  it('asks again after the form is cancelled, and goes away on "No thanks"', async () => {
    const user = renderOffer()
    await user.click(screen.getByRole('button', { name: 'Enter it' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByText('Shall I enter this as a session?')).toBeInTheDocument()
    expect(mockAdd).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'No thanks' }))
    await waitFor(() =>
      expect(screen.queryByText('Shall I enter this as a session?')).not.toBeInTheDocument()
    )
  })
})
