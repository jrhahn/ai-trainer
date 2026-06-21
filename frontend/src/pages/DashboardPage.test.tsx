import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import DashboardPage from './DashboardPage'
import {
  buildMatchCoachPrompt,
  computeMatchScore,
  matchScoreBadgeStyle,
  matchScoreLabel,
} from './DashboardPage'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

// ---------------------------------------------------------------------------
// Hoisted mocks
// ---------------------------------------------------------------------------

const { mockAdaptTrainingPlan, mockProcessPendingFeedbacks, mockRefreshLoginSummary } = vi.hoisted(() => ({
  mockAdaptTrainingPlan: vi.fn(),
  mockProcessPendingFeedbacks: vi.fn(),
  mockRefreshLoginSummary: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  adaptTrainingPlan: mockAdaptTrainingPlan,
  processPendingFeedbacks: mockProcessPendingFeedbacks,
  refreshLoginSummary: mockRefreshLoginSummary,
}))

vi.mock('../hooks/useStravaSync', () => ({ useStravaSync: vi.fn() }))

vi.mock('../hooks/useImportProgress', () => ({
  useImportProgress: () => ({ status: 'idle', total: 0, processed: 0, error: undefined }),
}))

vi.mock('../components/ProgressionChart', () => ({
  default: () => <div data-testid="progression-chart" />,
}))

vi.mock('../components/AIChat', () => ({
  default: () => <div data-testid="ai-chat" />,
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PREV_LOGIN_KEY = 'ai_trainer_previous_login'

const today = formatLocalDate(new Date())
const yesterday = formatLocalDate(new Date(Date.now() - 1 * 24 * 60 * 60 * 1000))
const twoDaysAgo = formatLocalDate(new Date(Date.now() - 2 * 24 * 60 * 60 * 1000))
const tomorrow = formatLocalDate(new Date(Date.now() + 1 * 24 * 60 * 60 * 1000))
const dayAfterTomorrow = formatLocalDate(new Date(Date.now() + 2 * 24 * 60 * 60 * 1000))
const threeDaysFromNow = formatLocalDate(new Date(Date.now() + 3 * 24 * 60 * 60 * 1000))
const fourDaysAgo = formatLocalDate(new Date(Date.now() - 4 * 24 * 60 * 60 * 1000))
const sixDaysAgo = formatLocalDate(new Date(Date.now() - 6 * 24 * 60 * 60 * 1000))

let rideIdCounter = 1
function makeRide(overrides: Partial<RideMetricPoint> & { activityDate: string }): RideMetricPoint {
  return {
    stravaActivityId: rideIdCounter++,
    sportType: 'Ride',
    activityName: 'Test Ride',
    durationSeconds: 3600,
    ...overrides,
  }
}

const baseProfile = {
  name: 'Test Athlete',
  email: 'test@example.com',
  bikeType: 'road' as const,
  trainingGoal: 'general_fitness' as const,
  weeklyHours: 8,
  followsTrainingPlan: false,
  fitnessLevel: 'intermediate' as const,
}

function setupStore(overrides: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  useAppStore.setState({
    authToken: 'test-token',
    userProfile: baseProfile,
    trainingPlan: [],
    rideMetricsHistory: [],
    riderAssessment: null,
    stravaConnection: null,
    isExpertMode: false,
    ...overrides,
  })
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>
  )
}

// ---------------------------------------------------------------------------
// Test lifecycle
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAppStore.getState().resetAll()
  localStorage.clear()
  vi.clearAllMocks()
  mockAdaptTrainingPlan.mockResolvedValue([])
  mockProcessPendingFeedbacks.mockResolvedValue('')
  mockRefreshLoginSummary.mockResolvedValue(null)
})

// ---------------------------------------------------------------------------
// Recent rides section
// ---------------------------------------------------------------------------

describe('DashboardPage — recent rides', () => {
  it('renders a ride from yesterday inside the 3-day window', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday, activityName: 'Morning Ride' })],
    })
    renderDashboard()
    expect(await screen.findByText('Morning Ride')).toBeInTheDocument()
  })

  it('renders a ride from today', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: today, activityName: "Today's Ride" })],
    })
    renderDashboard()
    expect(await screen.findByText("Today's Ride")).toBeInTheDocument()
  })

  it('does not render a ride older than 3 days by default', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: fourDaysAgo, activityName: 'Old Ride' })],
    })
    renderDashboard()
    // wait for effects to settle, then assert absence
    await waitFor(() => {
      expect(screen.queryByText('Old Ride')).not.toBeInTheDocument()
    })
  })

  it('formats the duration of a ride', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({ activityDate: yesterday, activityName: 'Long Ride', durationSeconds: 5400 }),
      ],
    })
    renderDashboard()
    expect(await screen.findByText('1h 30m')).toBeInTheDocument()
  })

  it('shows weather temperature for a ride', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: yesterday,
          activityName: 'Chilly Ride',
          weatherTemperatureC: 4.3,
          weatherCondition: 'cloudy',
        }),
      ],
    })
    renderDashboard()
    expect(await screen.findByText('4°C')).toBeInTheDocument()
  })

  it('renders the sport type label', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday, sportType: 'VirtualRide' })],
    })
    renderDashboard()
    expect(await screen.findByText('virtualride')).toBeInTheDocument()
  })

  it('deduplicates duplicate imported activity rows before rendering', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'weighttrainingKrafttraining',
          stravaActivityId: 7629419622326427000,
          externalActivityId: 'i157147093',
        }),
        makeRide({
          activityDate: today,
          activityName: 'weighttrainingKrafttraining',
          stravaActivityId: 7629419622326427463,
          externalActivityId: 'i157147093',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findByText('weighttrainingKrafttraining')).toBeInTheDocument()
    expect(screen.getAllByText('weighttrainingKrafttraining')).toHaveLength(1)
  })

  it('deduplicates identical visible activity rows even when imported ids differ', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'weighttrainingKrafttraining',
          sportType: 'WeightTraining',
          durationSeconds: 2220,
          stravaActivityId: 7629419622326427000,
          externalActivityId: 'intervals-activity-a',
        }),
        makeRide({
          activityDate: today,
          activityName: 'weighttrainingKrafttraining',
          sportType: 'WeightTraining',
          durationSeconds: 2220,
          stravaActivityId: 7629419622326427463,
          externalActivityId: 'intervals-activity-b',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findByText('weighttrainingKrafttraining')).toBeInTheDocument()
    expect(screen.getAllByText('weighttrainingKrafttraining')).toHaveLength(1)
    expect(screen.getByText('37 min')).toBeInTheDocument()
  })

  it('deduplicates same-day ride rows when source sport labels and start metadata differ', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Mountain Biking',
          sportType: 'Ride',
          durationSeconds: 3 * 60 * 60,
          activityStartDatetime: `${today}T08:03:21+02:00`,
          stravaActivityId: 9101,
          externalActivityId: 'strava-ride-9101',
        }),
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Mountain Biking',
          sportType: 'MountainBikeRide',
          durationSeconds: 3 * 60 * 60 + 12,
          activityStartDatetime: `${today}T08:03:00`,
          stravaActivityId: 9102,
          externalActivityId: 'intervals-ride-9102',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findByText('Darmstadt Mountain Biking')).toBeInTheDocument()
    expect(screen.getAllByText('Darmstadt Mountain Biking')).toHaveLength(1)
    expect(screen.getByText('3h 0m')).toBeInTheDocument()
  })

  it('deduplicates same-day ride rows when imported durations drift', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Mountain Biking',
          sportType: 'MountainBikeRide',
          durationSeconds: 96 * 60,
          activityStartDatetime: `${today}T08:03:21+02:00`,
          stravaActivityId: 9211,
          externalActivityId: 'strava-ride-9211',
        }),
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Mountain Biking',
          sportType: 'cycling',
          durationSeconds: 99 * 60,
          activityStartDatetime: `${today}T08:05:00`,
          stravaActivityId: 9212,
          externalActivityId: 'intervals-ride-9212',
        }),
        makeRide({
          activityDate: yesterday,
          activityName: 'Darmstadt Road Cycling',
          sportType: 'Ride',
          durationSeconds: 193 * 60,
          activityStartDatetime: `${yesterday}T09:00:00+02:00`,
          stravaActivityId: 9221,
          externalActivityId: 'strava-ride-9221',
        }),
        makeRide({
          activityDate: yesterday,
          activityName: 'Darmstadt Road Cycling',
          sportType: 'cycling',
          durationSeconds: 205 * 60,
          activityStartDatetime: `${yesterday}T09:08:00`,
          stravaActivityId: 9222,
          externalActivityId: 'intervals-ride-9222',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findByText('Darmstadt Mountain Biking')).toBeInTheDocument()
    expect(screen.getAllByText('Darmstadt Mountain Biking')).toHaveLength(1)
    expect(screen.getAllByText('Darmstadt Road Cycling')).toHaveLength(1)
  })

  it('keeps same-day equal-duration rides separate when start times differ', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'FIT Cycling',
          sportType: 'cycling',
          durationSeconds: 60 * 60,
          activityStartDatetime: `${today}T08:00:00+00:00`,
          stravaActivityId: 9201,
          externalActivityId: 'fit-ride-9201',
        }),
        makeRide({
          activityDate: today,
          activityName: 'FIT Cycling',
          sportType: 'cycling',
          durationSeconds: 60 * 60,
          activityStartDatetime: `${today}T10:00:00+00:00`,
          stravaActivityId: 9202,
          externalActivityId: 'fit-ride-9202',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findAllByText('FIT Cycling')).toHaveLength(2)
  })
})

// ---------------------------------------------------------------------------
// "new" badge logic
// ---------------------------------------------------------------------------

describe('DashboardPage — "new" badge', () => {
  it('shows "new" badge for a ride after the previous login date', async () => {
    localStorage.setItem(PREV_LOGIN_KEY, new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString())
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday, activityName: 'Fresh Ride' })],
    })
    renderDashboard()
    expect(await screen.findByText('new')).toBeInTheDocument()
  })

  it('does not show "new" badge when there is no previous login (first visit)', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday, activityName: 'First Visit Ride' })],
    })
    renderDashboard()
    expect(await screen.findByText('First Visit Ride')).toBeInTheDocument()
    expect(screen.queryByText('new')).not.toBeInTheDocument()
  })

  it('does not show "new" badge for a ride before the previous login date', async () => {
    // login happened after the ride
    localStorage.setItem(PREV_LOGIN_KEY, new Date().toISOString())
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: twoDaysAgo, activityName: 'Old Ride' })],
    })
    renderDashboard()
    expect(await screen.findByText('Old Ride')).toBeInTheDocument()
    expect(screen.queryByText('new')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 7-day window extension
// ---------------------------------------------------------------------------

describe('DashboardPage — 7-day window extension', () => {
  it('extends window to 7 days when a new activity exists between 3 and 7 days ago', async () => {
    // previous login was 6 days ago → activity 4 days ago is "new"
    localStorage.setItem(PREV_LOGIN_KEY, new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString())
    setupStore({
      rideMetricsHistory: [
        makeRide({ activityDate: fourDaysAgo, activityName: 'Extended Window Ride' }),
      ],
    })
    renderDashboard()
    expect(await screen.findByText('Extended Window Ride')).toBeInTheDocument()
    expect(screen.getByText('new')).toBeInTheDocument()
  })

  it('does not extend window when the activity beyond 3 days is not new', async () => {
    // previous login was yesterday → activity 4 days ago is NOT new
    localStorage.setItem(PREV_LOGIN_KEY, new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString())
    setupStore({
      rideMetricsHistory: [
        makeRide({ activityDate: fourDaysAgo, activityName: 'Old Out-of-Window Ride' }),
        makeRide({ activityDate: yesterday, activityName: 'Recent Ride' }),
      ],
    })
    renderDashboard()
    expect(await screen.findByText('Recent Ride')).toBeInTheDocument()
    expect(screen.queryByText('Old Out-of-Window Ride')).not.toBeInTheDocument()
  })

  it('does not extend window when there is no previous login', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({ activityDate: fourDaysAgo, activityName: 'Far Ride' }),
        makeRide({ activityDate: yesterday, activityName: 'Close Ride' }),
      ],
    })
    renderDashboard()
    expect(await screen.findByText('Close Ride')).toBeInTheDocument()
    expect(screen.queryByText('Far Ride')).not.toBeInTheDocument()
  })

  it('keeps window at 3 days when activity beyond 3 days is on exactly the previous login date', async () => {
    // previous login was exactly sixDaysAgo — activity also sixDaysAgo:
    // sixDaysAgo >= sevenDaysAgo ✓  |  sixDaysAgo < threeDaysAgo ✓  |  sixDaysAgo >= sixDaysAgo ✓
    // → should extend window
    localStorage.setItem(
      PREV_LOGIN_KEY,
      new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString()
    )
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: sixDaysAgo, activityName: 'Six-Day-Old Ride' })],
    })
    renderDashboard()
    expect(await screen.findByText('Six-Day-Old Ride')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Upcoming sub-label
// ---------------------------------------------------------------------------

describe('DashboardPage — Activities section layout', () => {
  it('refreshes a complete but stale login summary when new rides are present', async () => {
    localStorage.setItem(PREV_LOGIN_KEY, new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString())
    mockProcessPendingFeedbacks.mockResolvedValue(
      'Updated summary with the latest ride.\n- Recent activity: Today is now included.'
    )
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: 'Old but complete summary.\n- Recent activity: Yesterday only.',
      },
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Fresh Dashboard Ride',
          stravaActivityId: 9001,
        }),
      ],
    })

    renderDashboard()

    await waitFor(() => {
      expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('test-token', [9001])
      expect(screen.getByText('Updated summary with the latest ride.')).toBeInTheDocument()
    })
  })

  it('refreshes a stale summary for visible recent rides even after the previous login marker moved on', async () => {
    localStorage.setItem(PREV_LOGIN_KEY, new Date().toISOString())
    mockProcessPendingFeedbacks.mockResolvedValue(
      'Updated summary after revisiting the dashboard.\n- Latest activity: Oberursel MTB is included.'
    )
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: 'Old but complete summary.\n- Recent activity: Mittelberg Hiking.',
      },
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Oberursel (Taunus) Mountain Biking',
          stravaActivityId: 9002,
        }),
      ],
    })

    renderDashboard()

    await waitFor(() => {
      expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('test-token', [9002])
      expect(screen.getByText('Updated summary after revisiting the dashboard.')).toBeInTheDocument()
    })
  })

  it('summarizes only the latest visible recent ride when older visible rides exist', async () => {
    localStorage.setItem(PREV_LOGIN_KEY, new Date().toISOString())
    mockProcessPendingFeedbacks.mockResolvedValue(
      'Updated summary after latest ride.\n- Latest activity: Oberursel MTB is included.'
    )
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: 'Old summary.\n- Recent activity: Mittelberg Hiking.',
      },
      rideMetricsHistory: [
        makeRide({
          activityDate: twoDaysAgo,
          activityName: 'Mittelberg Hiking',
          stravaActivityId: 8001,
        }),
        makeRide({
          activityDate: today,
          activityStartDatetime: `${today}T15:00:00`,
          activityName: 'Oberursel (Taunus) Mountain Biking',
          stravaActivityId: 9004,
        }),
      ],
    })

    renderDashboard()

    await waitFor(() => {
      expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('test-token', [9004])
      expect(mockProcessPendingFeedbacks).not.toHaveBeenCalledWith('test-token', [8001, 9004])
    })
  })

  it('uses external activity ids for summary refresh when available', async () => {
    mockProcessPendingFeedbacks.mockResolvedValue(
      'Summary refreshed.\n- Latest activity: Oberursel MTB.'
    )
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: 'Old summary.\n- Latest activity: Mittelberg Hiking.',
      },
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Oberursel (Taunus) Mountain Biking',
          stravaActivityId: 7629419622326427000,
          externalActivityId: 'i157147093',
        }),
      ],
    })

    renderDashboard()

    await waitFor(() => {
      expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('test-token', ['i157147093'])
    })
  })

  it('does not refresh again once the visible recent activity set was summarized', async () => {
    localStorage.setItem('ai_trainer_summary_refresh_activity_ids', 'latest-activity-v6:strava:9003')
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: 'Already refreshed summary.\n- Latest activity: Oberursel MTB.',
      },
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Oberursel (Taunus) Mountain Biking',
          stravaActivityId: 9003,
        }),
      ],
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('Already refreshed summary.')).toBeInTheDocument()
    })
    expect(mockProcessPendingFeedbacks).not.toHaveBeenCalled()
  })

  it('renders JSON login summaries as formatted text instead of raw JSON', async () => {
    setupStore({
      riderAssessment: {
        riderType: 'allrounder',
        notes: '',
        loginSummary: JSON.stringify({
          intro: 'Great to see your recent effort.',
          bullets: [
            '- Ride Category: Your latest mountain bike ride was demanding.',
            '- Next Steps: Prioritize rest and recovery.',
          ],
        }),
      },
    })
    renderDashboard()

    expect(await screen.findByText('Great to see your recent effort.')).toBeInTheDocument()
    expect(screen.getByText('Ride Category:')).toBeInTheDocument()
    expect(screen.getByText('Your latest mountain bike ride was demanding.')).toBeInTheDocument()
    expect(screen.queryByText(/"intro"/)).not.toBeInTheDocument()
  })

  it('shows "Upcoming" sub-label when both recent rides and plan days exist', async () => {
    const planDay: TrainingDay = {
      date: tomorrow,
      workoutType: 'endurance',
      title: 'Easy Z2',
      description: 'Zone 2 ride',
      durationMinutes: 60,
    }
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday })],
      trainingPlan: [planDay],
    })
    renderDashboard()
    expect(await screen.findByText('Upcoming')).toBeInTheDocument()
  })

  it('does not show "Upcoming" sub-label when there are no recent rides', async () => {
    const planDay: TrainingDay = {
      date: tomorrow,
      workoutType: 'endurance',
      title: 'Easy Z2',
      description: 'Zone 2 ride',
      durationMinutes: 60,
    }
    setupStore({
      rideMetricsHistory: [],
      trainingPlan: [planDay],
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByText('Upcoming')).not.toBeInTheDocument()
    })
  })

  it('renders nothing when there are no rides and no plan', async () => {
    setupStore({ rideMetricsHistory: [], trainingPlan: [] })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByText('Activities')).not.toBeInTheDocument()
    })
  })

  it('shows the Activities section header when rides exist', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday })],
    })
    renderDashboard()
    expect(await screen.findByText('Activities')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// ProgressionChart visibility
// ---------------------------------------------------------------------------

describe('DashboardPage — ProgressionChart', () => {
  it('does not render ProgressionChart in normal mode', async () => {
    setupStore({ isExpertMode: false })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByTestId('progression-chart')).not.toBeInTheDocument()
    })
  })

  it('renders ProgressionChart when expert mode is on', async () => {
    setupStore({ isExpertMode: true })
    renderDashboard()
    expect(await screen.findByTestId('progression-chart')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Plan comparison row & score badge
// ---------------------------------------------------------------------------

describe('DashboardPage — plan comparison row', () => {
  it('shows plan title and score badge for a matched ride', async () => {
    const matchedRide = makeRide({
      activityDate: yesterday,
      activityName: 'Evening Ride',
      durationSeconds: 3600,
      normalizedPowerW: 290,
      planMatchStatus: 'auto_matched',
      matchedPlanSnapshot: {
        title: 'Tempo Intervals',
        workoutType: 'tempo',
        durationMinutes: 60,
        targetPower: { low: 270, high: 310 },
      },
    })
    setupStore({ rideMetricsHistory: [matchedRide] })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Tempo Intervals/)).toBeInTheDocument()
    // Score badge should show a label
    const badge = await screen.findByTitle('Ask coach about this match')
    expect(['Perfect', 'Solid', 'Close', 'Off plan', 'Needs work', '?']).toContain(badge.textContent)
  })

  it('shows when no planned workout can be found for an activity date', async () => {
    const unmatchedRide = makeRide({
      activityDate: yesterday,
      activityName: 'Free Ride',
      planMatchStatus: 'unmatched',
      matchedPlanSnapshot: null,
    })
    setupStore({ rideMetricsHistory: [unmatchedRide] })
    renderDashboard()

    expect(await screen.findByText('Free Ride')).toBeInTheDocument()
    expect(screen.getByText('planned:')).toBeInTheDocument()
    expect(screen.getByText('No planned workout found')).toBeInTheDocument()
    expect(screen.queryByTitle('Ask coach about this match')).not.toBeInTheDocument()
  })

  it('falls back to the same-date training plan when the ride has no matched snapshot', async () => {
    const plannedRide = makeRide({
      activityDate: yesterday,
      activityName: 'Morning Mountain Bike Ride',
      planMatchStatus: 'unmatched',
      matchedPlanSnapshot: null,
    })
    setupStore({
      rideMetricsHistory: [plannedRide],
      trainingPlan: [
        {
          date: yesterday,
          workoutType: 'endurance',
          title: 'Endurance Base Ride',
          description: 'Keep it steady',
          durationMinutes: 90,
          completed: true,
        },
        {
          date: today,
          workoutType: 'rest',
          title: 'Complete Rest Day',
          description: 'No training planned',
          durationMinutes: 0,
        },
      ],
    })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Endurance Base Ride/)).toBeInTheDocument()
  })

  it('ignores stale matched snapshots from a different planned date', async () => {
    const rideWithStaleSnapshot = makeRide({
      activityDate: yesterday,
      activityName: 'Afternoon Ride',
      planMatchStatus: 'auto_matched',
      matchedPlanDate: today,
      matchedPlanSnapshot: {
        date: today,
        title: 'Complete Rest Day',
        workoutType: 'rest',
        durationMinutes: 0,
      },
    })
    setupStore({
      rideMetricsHistory: [rideWithStaleSnapshot],
      trainingPlan: [
        {
          date: yesterday,
          workoutType: 'endurance',
          title: 'Endurance Base Ride',
          description: 'Keep it steady',
          durationMinutes: 90,
          completed: true,
        },
        {
          date: today,
          workoutType: 'rest',
          title: 'Complete Rest Day',
          description: 'No training planned',
          durationMinutes: 0,
        },
      ],
    })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Endurance Base Ride/)).toBeInTheDocument()
  })

  it('shows the planned row for unmatched rides with same-day plan context', async () => {
    const restDayRide = makeRide({
      activityDate: yesterday,
      activityName: 'Bonus Spin',
      planMatchStatus: 'unmatched',
      matchedPlanDate: yesterday,
      matchedPlanSnapshot: {
        title: 'Complete Rest Day',
        workoutType: 'rest',
        durationMinutes: 0,
      },
    })
    setupStore({ rideMetricsHistory: [restDayRide] })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Complete Rest Day/)).toBeInTheDocument()
    expect(screen.getByTitle('Ask coach about this match')).toHaveTextContent('OK')
  })

  it('uses the current same-date training plan over an older matched snapshot', async () => {
    const rideWithOldSnapshot = makeRide({
      activityDate: yesterday,
      activityName: 'Friday Mountain Bike Ride',
      planMatchStatus: 'auto_matched',
      matchedPlanDate: yesterday,
      matchedPlanSnapshot: {
        date: yesterday,
        title: 'Complete Rest Day',
        workoutType: 'rest',
        durationMinutes: 0,
      },
    })
    setupStore({
      rideMetricsHistory: [rideWithOldSnapshot],
      trainingPlan: [
        {
          date: yesterday,
          workoutType: 'recovery',
          title: 'Easy Recovery Spin',
          description: 'Keep it easy',
          durationMinutes: 45,
          completed: true,
        },
      ],
    })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Easy Recovery Spin/)).toBeInTheDocument()
    expect(screen.queryByText(/Complete Rest Day/)).not.toBeInTheDocument()
  })

  it('removes today from Upcoming when an activity already exists today', async () => {
    const todaysRide = makeRide({
      activityDate: today,
      activityName: 'Today Ride',
      planMatchStatus: 'auto_matched',
      matchedPlanDate: today,
      matchedPlanSnapshot: {
        date: today,
        title: 'Complete Rest Day',
        workoutType: 'rest',
        durationMinutes: 0,
      },
    })
    setupStore({
      rideMetricsHistory: [todaysRide],
      trainingPlan: [
        {
          date: today,
          workoutType: 'recovery',
          title: 'Today Recovery Spin',
          description: 'Easy spin',
          durationMinutes: 45,
        },
        {
          date: tomorrow,
          workoutType: 'intervals',
          title: 'Tomorrow VO2 Max Intervals',
          description: 'Hard intervals',
          durationMinutes: 60,
        },
        {
          date: dayAfterTomorrow,
          workoutType: 'rest',
          title: 'Rest Day',
          description: 'Recover',
          durationMinutes: 0,
        },
        {
          date: threeDaysFromNow,
          workoutType: 'endurance',
          title: 'Endurance Ride',
          description: 'Zone 2',
          durationMinutes: 90,
        },
      ],
    })
    renderDashboard()

    expect(await screen.findByText('Today Ride')).toBeInTheDocument()
    expect(screen.getAllByText(/Today Recovery Spin/)).toHaveLength(1)
    expect(screen.getByText('Upcoming')).toBeInTheDocument()
    expect(screen.getByText(/Tomorrow VO2 Max Intervals/)).toBeInTheDocument()
    expect(screen.getByText(/Rest Day/)).toBeInTheDocument()
    expect(screen.getByText(/Endurance Ride/)).toBeInTheDocument()
  })

  it('clicking the score badge sets pendingCoachMessage in the store', async () => {
    const matchedRide = makeRide({
      activityDate: yesterday,
      activityName: 'Hill Repeats',
      durationSeconds: 3600,
      planMatchStatus: 'auto_matched',
      matchedPlanSnapshot: {
        title: 'Hill Session',
        workoutType: 'intervals',
        durationMinutes: 60,
      },
    })
    setupStore({ rideMetricsHistory: [matchedRide] })
    renderDashboard()

    const badge = await screen.findByTitle('Ask coach about this match')
    badge.click()

    const msg = useAppStore.getState().pendingCoachMessage
    expect(msg).not.toBeNull()
    expect(msg).toContain('Hill Repeats')
    expect(msg).toContain('Hill Session')
  })
})

// ---------------------------------------------------------------------------
// computeMatchScore — unit tests
// ---------------------------------------------------------------------------

describe('computeMatchScore — rest/no-target plan', () => {
  // A plan that triggers isRestOrNoTargetPlan: workoutType 'rest' (LLM may assign this to Active Recovery)
  const restPlanWithDuration: Partial<TrainingDay> = { workoutType: 'rest', durationMinutes: 45 }
  const restPlanNoData: Partial<TrainingDay> = { workoutType: 'rest' }

  it('returns ~0 for a 5h25m ride vs a 45-min rest plan (TSS null)', () => {
    const ride = { stravaActivityId: 1, sportType: 'MountainBikeRide', durationSeconds: 19500 } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanWithDuration)
    expect(score).toBe(0)
  })

  it('returns 80 when TSS ≤ 30 on a rest plan', () => {
    const ride = { stravaActivityId: 2, sportType: 'Ride', durationSeconds: 2700, tss: 20 } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanNoData)
    expect(score).toBe(80)
  })

  it('returns 90 (no-data fallback) for a rest plan with no duration data on either side', () => {
    const ride = { stravaActivityId: 3, sportType: 'Ride' } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanNoData)
    expect(score).toBe(90)
  })

  it('returns close to 100 for a ride matching planned duration with no TSS', () => {
    // 44 min on a 45-min plan — should score high (ratio ≈ 0.978)
    const ride = { stravaActivityId: 4, sportType: 'Ride', durationSeconds: 2640 } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanWithDuration)
    expect(score).toBeGreaterThanOrEqual(95)
  })

  it('label for score=0 on rest plan is "Too much", not "OK"', () => {
    const ride = { stravaActivityId: 5, sportType: 'MountainBikeRide', durationSeconds: 19500 } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanWithDuration)
    expect(matchScoreLabel(score, restPlanWithDuration)).toBe('Too much')
  })

  it('label for score=90 on rest plan with no data is "OK"', () => {
    const ride = { stravaActivityId: 6, sportType: 'Ride' } as RideMetricPoint
    const score = computeMatchScore(ride, restPlanNoData)
    expect(matchScoreLabel(score, restPlanNoData)).toBe('OK')
  })

  it('uses informational styling for additional-ride override labels', () => {
    expect(matchScoreBadgeStyle(35, restPlanWithDuration, 'Additional')).toContain(
      'bg-blue-100'
    )
  })
})

describe('computeMatchScore — endurance plan', () => {
  const endurancePlan: Partial<TrainingDay> = {
    workoutType: 'endurance',
    title: 'Long Endurance Ride with Climbing Focus',
    durationMinutes: 180,
  }

  it('rates a 3h25 ride against a 3h endurance plan as close, not too much', () => {
    const ride = {
      stravaActivityId: 7,
      sportType: 'Ride',
      durationSeconds: 205 * 60,
      tss: 140,
    } as RideMetricPoint
    const score = computeMatchScore(ride, endurancePlan)
    expect(score).toBeGreaterThanOrEqual(60)
    expect(matchScoreLabel(score, endurancePlan)).toBe('Close')
  })

  it('anchors coach match prompts to the displayed label and score', () => {
    const ride = {
      stravaActivityId: 8,
      activityName: 'Darmstadt Road Cycling',
      sportType: 'Ride',
      durationSeconds: 205 * 60,
    } as RideMetricPoint
    const score = computeMatchScore(ride, endurancePlan)
    const prompt = buildMatchCoachPrompt(ride, endurancePlan, score)

    expect(prompt).toContain('The displayed match label is "Close"')
    expect(prompt).toContain('with a 72% score')
    expect(prompt).toContain('do not invent data-quality causes')
  })
})
