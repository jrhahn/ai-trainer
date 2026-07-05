import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import WorkoutPage from './WorkoutPage'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'

const { mockRateCompletedWorkout, mockFetchTrainingPlan, mockSaveTrainingPlan, mockSaveWorkoutLog, mockFetchPlanHistory } = vi.hoisted(() => ({
  mockRateCompletedWorkout: vi.fn(),
  mockFetchTrainingPlan: vi.fn(),
  mockSaveTrainingPlan: vi.fn(),
  mockSaveWorkoutLog: vi.fn(),
  mockFetchPlanHistory: vi.fn(),
}))

vi.mock('../services/ai', () => ({ rateCompletedWorkout: mockRateCompletedWorkout }))
vi.mock('../services/user', () => ({
  fetchTrainingPlan: mockFetchTrainingPlan,
  saveTrainingPlan: mockSaveTrainingPlan,
  saveWorkoutLog: mockSaveWorkoutLog,
  fetchPlanHistory: mockFetchPlanHistory,
}))

// AIChat is heavy - stub it out
vi.mock('../components/AIChat', () => ({
  default: () => <div data-testid="ai-chat-stub" />,
}))

const TODAY = '2025-01-15'
const mockDay: TrainingDay = {
  date: TODAY,
  workoutType: 'intervals',
  title: 'VO2max Intervals',
  description: '5x4min at 120% FTP',
  durationMinutes: 60,
  targetPower: { low: 300, high: 340 },
}

function renderWorkoutPage(date: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={[`/workout/${date}`]}>
        <Routes>
          <Route path="/workout/:date" element={<WorkoutPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockRateCompletedWorkout.mockResolvedValue('Great session!')
  mockFetchTrainingPlan.mockResolvedValue([mockDay])
  mockSaveTrainingPlan.mockResolvedValue([])
  mockSaveWorkoutLog.mockResolvedValue(undefined)
  mockFetchPlanHistory.mockResolvedValue([])
})

describe('WorkoutPage', () => {
  it('shows the change history for a date outside the current plan window', async () => {
    // A completed/past day gets pruned from the rolling plan window, but its
    // history still exists — the page must surface it, not dead-end (#357).
    const prunedDate = '2099-01-01'
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    mockFetchPlanHistory.mockResolvedValue([
      {
        id: 'h1',
        date: prunedDate,
        source: 'coach_chat',
        applied: true,
        recordedAt: '2099-01-01T10:00:00Z',
        oldDay: null,
        newDay: { workoutType: 'endurance', title: 'Zone 2 Ride' },
      },
    ])

    renderWorkoutPage(prunedDate)

    // The dead-end message is gone; the fallback explains the day left the plan.
    expect(screen.queryByText('Workout not found.')).not.toBeInTheDocument()
    expect(
      screen.getByText(/no longer part of your current plan/i),
    ).toBeInTheDocument()

    // History auto-loads for an out-of-window day (no manual expand needed).
    expect(
      await screen.findByText('Coach chat: Added endurance — Zone 2 Ride'),
    ).toBeInTheDocument()
    expect(mockFetchPlanHistory).toHaveBeenCalledWith('tok', prunedDate)
  })

  it('renders workout details when the day exists in the plan', () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    expect(screen.getByText('VO2max Intervals')).toBeInTheDocument()
    expect(screen.getByText('5x4min at 120% FTP')).toBeInTheDocument()
    expect(screen.getByText(/60 minutes/)).toBeInTheDocument()
    expect(screen.getByText(/300.*340W/)).toBeInTheDocument()
  })

  it('shows the Log Completed Workout button for an incomplete non-rest workout', () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    expect(screen.getByRole('button', { name: /log completed workout/i })).toBeInTheDocument()
  })

  it('does not show the Log button for a rest day', () => {
    const restDay: TrainingDay = { ...mockDay, workoutType: 'rest', title: 'Rest Day' }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [restDay] })
    renderWorkoutPage(TODAY)

    expect(screen.queryByRole('button', { name: /log completed workout/i })).not.toBeInTheDocument()
  })

  it('shows the workout feedback form after clicking Log Completed Workout', async () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    await userEvent.click(screen.getByRole('button', { name: /log completed workout/i }))

    expect(screen.getByText('Log Workout')).toBeInTheDocument()
    expect(screen.getByText(/Duration \(minutes\)/i)).toBeInTheDocument()
  })

  it('logs the workout and requests coach feedback after submitting the feedback form', async () => {
    useAppStore.setState({
      authToken: 'tok-123',
      trainingPlan: [mockDay],
      userProfile: {
        name: 'Alice',
        email: 'alice@example.com',
        bikeType: 'road',
        trainingGoal: 'general_fitness',
        followsTrainingPlan: true,
        fitnessLevel: 'intermediate',
      },
    })
    renderWorkoutPage(TODAY)

    await userEvent.click(screen.getByRole('button', { name: /log completed workout/i }))
    await userEvent.click(screen.getByRole('button', { name: /save workout/i }))

    await waitFor(() => {
      expect(mockSaveWorkoutLog).toHaveBeenCalledWith('tok-123', TODAY, expect.anything())
      expect(mockRateCompletedWorkout).toHaveBeenCalled()
    })
  })

  it('shows existing workout feedback summary when the day is already completed', () => {
    const completedDay: TrainingDay = {
      ...mockDay,
      completed: true,
      feedback: {
        actualDurationMinutes: 55,
        perceivedEffort: 4,
        notes: 'Really tough session',
        completedAt: '2025-01-15T10:00:00Z',
      },
      coachFeedback: 'Excellent effort — threshold power looks strong.',
    }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [completedDay] })
    renderWorkoutPage(TODAY)

    expect(screen.getByText('Your Workout Log')).toBeInTheDocument()
    expect(screen.getByText(/55 min/)).toBeInTheDocument()
    expect(screen.getByText('Really tough session')).toBeInTheDocument()
    expect(screen.getByText(/Excellent effort/)).toBeInTheDocument()
  })

  it('renders workoutPurpose section when the field is present', () => {
    const dayWithPurpose: TrainingDay = {
      ...mockDay,
      workoutPurpose: 'Raises VO2max by stressing the cardiovascular system.',
    }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [dayWithPurpose] })
    renderWorkoutPage(TODAY)

    expect(screen.getByText('Why this workout')).toBeInTheDocument()
    expect(screen.getByText('Raises VO2max by stressing the cardiovascular system.')).toBeInTheDocument()
  })

  it('does not render workoutPurpose section when the field is absent', () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    expect(screen.queryByText('Why this workout')).not.toBeInTheDocument()
  })

  it('renders keyFocusPoints section when the field is present', () => {
    const dayWithFocus: TrainingDay = {
      ...mockDay,
      keyFocusPoints: ['Keep cadence above 90 rpm', 'HR must stay below 158 bpm', 'Breathe rhythmically'],
    }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [dayWithFocus] })
    renderWorkoutPage(TODAY)

    expect(screen.getByText('Key Focus Points')).toBeInTheDocument()
    expect(screen.getByText('Keep cadence above 90 rpm')).toBeInTheDocument()
    expect(screen.getByText('HR must stay below 158 bpm')).toBeInTheDocument()
    expect(screen.getByText('Breathe rhythmically')).toBeInTheDocument()
  })

  it('does not render keyFocusPoints section when the field is absent', () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    expect(screen.queryByText('Key Focus Points')).not.toBeInTheDocument()
  })

  it('shows the regenerate plan nudge when workoutPurpose is absent for a non-rest day', () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    renderWorkoutPage(TODAY)

    expect(screen.getByText(/Regenerate your plan/i)).toBeInTheDocument()
  })

  it('does not show the regenerate plan nudge when workoutPurpose is present', () => {
    const dayWithPurpose: TrainingDay = {
      ...mockDay,
      workoutPurpose: 'Builds aerobic base.',
    }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [dayWithPurpose] })
    renderWorkoutPage(TODAY)

    expect(screen.queryByText(/Regenerate your plan/i)).not.toBeInTheDocument()
  })

  it('does not show the regenerate plan nudge for rest days', () => {
    const restDay: TrainingDay = { ...mockDay, workoutType: 'rest', title: 'Rest Day' }
    useAppStore.setState({ authToken: 'tok', trainingPlan: [restDay] })
    renderWorkoutPage(TODAY)

    expect(screen.queryByText(/Regenerate your plan/i)).not.toBeInTheDocument()
  })

  it('saves coachFeedback against the latest backend plan, not the local store snapshot', async () => {
    const otherDay: TrainingDay = {
      date: '2025-01-16',
      workoutType: 'endurance',
      title: 'Endurance Ride',
      description: 'Easy zone 2 ride',
      durationMinutes: 90,
    }
    // Local store has a stale title for TODAY's workout
    const staleLocalPlan: TrainingDay[] = [{ ...mockDay, title: 'Stale Local Title' }, otherDay]
    // Backend has the current plan with an updated title
    const freshBackendPlan: TrainingDay[] = [{ ...mockDay, title: 'Fresh Backend Title' }, otherDay]

    useAppStore.setState({
      authToken: 'tok-123',
      trainingPlan: staleLocalPlan,
      userProfile: {
        name: 'Alice',
        email: 'alice@example.com',
        bikeType: 'road',
        trainingGoal: 'general_fitness',
        followsTrainingPlan: true,
        fitnessLevel: 'intermediate',
      },
    })
    mockRateCompletedWorkout.mockResolvedValue({
      feedback: 'Great effort today!',
      needsAthleteFeedback: false,
      followUpQuestion: null,
      suggestedFeedbackTags: [],
    })
    mockFetchTrainingPlan.mockResolvedValue(freshBackendPlan)

    renderWorkoutPage(TODAY)
    await userEvent.click(screen.getByRole('button', { name: /log completed workout/i }))
    await userEvent.click(screen.getByRole('button', { name: /save workout/i }))

    await waitFor(() => {
      expect(mockFetchTrainingPlan).toHaveBeenCalledWith('tok-123')
      // Must save with fresh backend plan, not the stale local snapshot
      expect(mockSaveTrainingPlan).toHaveBeenCalledWith('tok-123', [
        { ...freshBackendPlan[0], coachFeedback: 'Great effort today!' },
        otherDay,
      ])
    })
  })

  it('loads the change history for the day when expanded, marking blocked attempts', async () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    mockFetchPlanHistory.mockResolvedValue([
      {
        id: 'h1',
        date: TODAY,
        source: 'coach_chat',
        applied: true,
        recordedAt: '2025-01-15T10:00:00Z',
        oldDay: null,
        newDay: { workoutType: 'intervals', title: 'VO2max Intervals' },
      },
      {
        id: 'h2',
        date: TODAY,
        source: 'auto_adapt',
        applied: false,
        recordedAt: '2025-01-15T12:00:00Z',
        oldDay: { workoutType: 'intervals' },
        newDay: { workoutType: 'recovery' },
      },
    ])

    renderWorkoutPage(TODAY)
    // History is lazy — only fetched once the section is opened.
    expect(mockFetchPlanHistory).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /change history/i }))

    expect(await screen.findByText('Coach chat: Added intervals — VO2max Intervals')).toBeInTheDocument()
    expect(
      screen.getByText('Auto-adaptation wanted to type intervals → recovery but kept your version'),
    ).toBeInTheDocument()
    expect(mockFetchPlanHistory).toHaveBeenCalledWith('tok', TODAY)
  })

  it('shows an empty-state message when the day has no recorded changes', async () => {
    useAppStore.setState({ authToken: 'tok', trainingPlan: [mockDay] })
    mockFetchPlanHistory.mockResolvedValue([])

    renderWorkoutPage(TODAY)
    await userEvent.click(screen.getByRole('button', { name: /change history/i }))

    expect(
      await screen.findByText('No recorded changes for this day yet.'),
    ).toBeInTheDocument()
  })
})
