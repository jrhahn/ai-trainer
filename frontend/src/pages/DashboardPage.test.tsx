import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import DashboardPage from './DashboardPage'
import {
  buildMatchCoachPrompt,
  computeMatchScore,
  matchScoreBadgeStyle,
  matchScoreLabel,
} from './DashboardPage'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, RiderAssessment, TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

// ---------------------------------------------------------------------------
// Hoisted mocks
// ---------------------------------------------------------------------------

const {
  mockProcessPendingFeedbacks,
  mockRefreshLoginSummary,
  mockRefreshTrainingStatus,
  mockSetRideLegs,
  mockUseImportProgress,
} = vi.hoisted(() => ({
  mockProcessPendingFeedbacks: vi.fn(),
  mockRefreshLoginSummary: vi.fn(),
  mockRefreshTrainingStatus: vi.fn(),
  mockSetRideLegs: vi.fn(),
  mockUseImportProgress: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  processPendingFeedbacks: mockProcessPendingFeedbacks,
  refreshLoginSummary: mockRefreshLoginSummary,
  refreshTrainingStatus: mockRefreshTrainingStatus,
}))

vi.mock('../services/user', () => ({
  setRideLegs: mockSetRideLegs,
}))

vi.mock('../hooks/useStravaSync', () => ({ useStravaSync: vi.fn() }))

vi.mock('../hooks/useImportProgress', () => ({
  useImportProgress: mockUseImportProgress,
}))

const idleImportProgress = {
  status: 'idle' as const,
  total: 0,
  processed: 0,
  imported: 0,
  skipped: 0,
  failedActivities: [],
  error: '',
}

const stravaConnection = { athleteId: 123, athleteName: 'Test Athlete' }

vi.mock('../components/ProgressionChart', () => ({
  default: () => <div data-testid="progression-chart" />,
}))

vi.mock('../components/PlanChangesPanel', () => ({
  default: () => <div data-testid="plan-changes-panel" />,
}))

vi.mock('../components/TrainingCalendar', () => ({
  default: () => <div data-testid="training-calendar" />,
}))

vi.mock('../components/AIChat', () => ({
  default: () => <div data-testid="ai-chat" />,
}))

vi.mock('../components/AthletePerformanceModelCard', () => ({
  default: () => <div data-testid="performance-model-card" />,
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

/** The week strip's accessible name for a day, e.g. "Tuesday — Rest" (#634).
 *  Derived rather than hard-coded, since which weekday `tomorrow` lands on
 *  depends on when the suite runs. */
const weekdayOf = (date: string, label: string) =>
  `${new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })} — ${label}`

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

function assessmentWithStatus(
  overrides: Partial<RiderAssessment> = {}
): RiderAssessment {
  return {
    riderType: 'allrounder',
    notes: '',
    trainingStatusLabel: 'On track',
    trainingStatusTone: 'positive',
    trainingStatusRationale: 'Every planned session is done.',
    ...overrides,
  }
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
  mockProcessPendingFeedbacks.mockResolvedValue('')
  mockRefreshLoginSummary.mockResolvedValue(null)
  mockRefreshTrainingStatus.mockResolvedValue(null)
  mockSetRideLegs.mockResolvedValue({ stravaActivityId: 0, ride: null })
  mockUseImportProgress.mockReturnValue(idleImportProgress)
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

  it('deduplicates contained same-day ride imports and keeps the longer ride', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Mountain Biking',
          sportType: 'MountainBikeRide',
          durationSeconds: 63 * 60,
          activityStartDatetime: `${today}T09:04:00`,
          stravaActivityId: 9231,
          externalActivityId: 'intervals-ride-9231',
        }),
        makeRide({
          activityDate: today,
          activityName: 'Darmstadt Road Cycling',
          sportType: 'cycling',
          durationSeconds: 205 * 60,
          activityStartDatetime: `${today}T09:00:00`,
          stravaActivityId: 9232,
          externalActivityId: 'strava-ride-9232',
        }),
      ],
    })
    renderDashboard()

    expect(await screen.findByText('Darmstadt Road Cycling')).toBeInTheDocument()
    expect(screen.queryByText('Darmstadt Mountain Biking')).not.toBeInTheDocument()
    expect(screen.getByText('3h 25m')).toBeInTheDocument()
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

  // The upcoming-plan preview that used to live under "Activities" is gone —
  // WeekStrip shows the same days without truncating their names, and this
  // section is now only rides that actually happened.
  it('shows upcoming plan days in the week strip, not in the rides list', async () => {
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
    expect(await screen.findByText('This week')).toBeInTheDocument()
    expect(screen.queryByText('Upcoming')).not.toBeInTheDocument()
  })

  // The plan used to reach the dashboard only through the rides list, so an
  // athlete who had ridden nothing yet saw no plan either.  The two are now
  // independent: the week is shown because a plan exists, not because a ride does.
  it('shows the week even when no ride has been logged yet', async () => {
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
    expect(await screen.findByText('This week')).toBeInTheDocument()
    // Selecting tomorrow surfaces it in the hero, which is a stronger claim than
    // the old link check: the plan reached the dashboard *and* is readable there.
    await userEvent.click(screen.getByRole('button', { name: weekdayOf(tomorrow, 'Endurance') }))
    expect(await screen.findByRole('heading', { name: 'Easy Z2' })).toBeInTheDocument()
    // ...and no empty "Recent rides" shell above it.
    expect(screen.queryByText('Recent rides')).not.toBeInTheDocument()
  })

  it('renders nothing when there are no rides and no plan', async () => {
    setupStore({ rideMetricsHistory: [], trainingPlan: [] })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByText('Recent rides')).not.toBeInTheDocument()
    })
  })

  it('shows the recent-rides header when rides exist', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ activityDate: yesterday })],
    })
    renderDashboard()
    expect(await screen.findByText('Recent rides')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// ProgressionChart visibility
// ---------------------------------------------------------------------------

describe('DashboardPage — ProgressionChart', () => {
  // A diagnostic, not a daily read.  #619 promoted it onto the front page and
  // #621 put it back: the default view answers what is on, how it has gone and
  // how to reach the coach, and form curves are none of those.
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

describe('DashboardPage — PlanChangesPanel', () => {
  it('does not render the plan-changes panel in normal mode', async () => {
    setupStore({ isExpertMode: false })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByTestId('plan-changes-panel')).not.toBeInTheDocument()
    })
  })

  it('renders the plan-changes panel when expert mode is on', async () => {
    setupStore({ isExpertMode: true })
    renderDashboard()
    expect(await screen.findByTestId('plan-changes-panel')).toBeInTheDocument()
  })
})

// The calendar reaches the page through the week strip's "Show more" and
// nowhere else (#621) — it used to also sit inline further down, which showed
// the same month twice.
describe('DashboardPage — TrainingCalendar', () => {
  const planned: TrainingDay = {
    date: today,
    workoutType: 'endurance',
    title: 'Zone 2',
    description: 'Steady',
    durationMinutes: 90,
  }

  it('keeps the calendar off the page until it is asked for', async () => {
    setupStore({ isExpertMode: true, trainingPlan: [planned] })
    renderDashboard()
    expect(await screen.findByText('This week')).toBeInTheDocument()
    expect(screen.queryByTestId('training-calendar')).not.toBeInTheDocument()
  })

  it('opens the calendar as a dialog from the week strip', async () => {
    setupStore({ trainingPlan: [planned] })
    renderDashboard()

    await userEvent.click(await screen.findByRole('button', { name: 'Show more' }))

    const dialog = screen.getByRole('dialog', { name: 'Training calendar' })
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByTestId('training-calendar')).toBeInTheDocument()
  })

  it('closes the calendar again on Escape', async () => {
    setupStore({ trainingPlan: [planned] })
    renderDashboard()

    await userEvent.click(await screen.findByRole('button', { name: 'Show more' }))
    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('dialog', { name: 'Training calendar' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('training-calendar')).not.toBeInTheDocument()
  })
})

describe('DashboardPage — athlete performance model', () => {
  it('does not render the performance model card in normal mode', async () => {
    setupStore({ isExpertMode: false })
    renderDashboard()
    await waitFor(() => {
      expect(screen.queryByTestId('performance-model-card')).not.toBeInTheDocument()
    })
  })

  it('renders the performance model card when expert mode is on', async () => {
    setupStore({ isExpertMode: true })
    renderDashboard()
    expect(await screen.findByTestId('performance-model-card')).toBeInTheDocument()
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

  it('shows the logged ride and the plan it matched, without a duplicate upcoming list', async () => {
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
    // Today's plan appears on the matched ride card and as the hero heading.
    expect(screen.getAllByText(/Today Recovery Spin/)).toHaveLength(2)
    // The upcoming preview is gone; the week strip carries those days instead,
    // so each future session is named once rather than in two competing lists.
    expect(screen.queryByText('Upcoming')).not.toBeInTheDocument()
    expect(screen.queryByText(/Tomorrow VO2 Max Intervals/)).not.toBeInTheDocument()
    expect(screen.getByText('This week')).toBeInTheDocument()
    // Not listed a second time, but one click away in the hero (#634).
    await userEvent.click(screen.getByRole('button', { name: weekdayOf(tomorrow, 'Intervals') }))
    expect(
      await screen.findByRole('heading', { name: 'Tomorrow VO2 Max Intervals' })
    ).toBeInTheDocument()
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

  it('scores an in-window ride on a duration range as fully on-target (#368)', () => {
    // 2.75h ride vs a 2.5–3h endurance window → any in-range duration is on-target.
    const rangePlan: Partial<TrainingDay> = {
      workoutType: 'rest',
      durationMinutes: 165,
      durationMinMinutes: 150,
      durationMaxMinutes: 180,
    }
    const ride = { stravaActivityId: 9, sportType: 'Ride', durationSeconds: 165 * 60 } as RideMetricPoint
    expect(computeMatchScore(ride, rangePlan)).toBe(100)
  })
})

describe('computeMatchScore — strength plan', () => {
  const strengthPlan: Partial<TrainingDay> = { workoutType: 'strength', durationMinutes: 60 }

  it('scores purely on duration completion', () => {
    const ride = { stravaActivityId: 10, sportType: 'WeightTraining', durationSeconds: 30 * 60 } as RideMetricPoint
    // 30 min of a 60-min plan → 50 %.
    expect(computeMatchScore(ride, strengthPlan)).toBe(50)
  })

  it('caps a longer-than-planned session at 100 %', () => {
    const ride = { stravaActivityId: 11, sportType: 'WeightTraining', durationSeconds: 90 * 60 } as RideMetricPoint
    expect(computeMatchScore(ride, strengthPlan)).toBe(100)
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

describe('DashboardPage — leg-feel control', () => {
  it('persists a leg-feel tap and optimistically updates the store', async () => {
    setupStore({
      rideMetricsHistory: [makeRide({ stravaActivityId: 555, activityDate: yesterday })],
    })
    renderDashboard()

    const heavy = await screen.findByRole('button', { name: 'Legs felt Heavy' })
    fireEvent.click(heavy)

    expect(mockSetRideLegs).toHaveBeenCalledWith('test-token', 555, 'heavy', undefined)
    await waitFor(() =>
      expect(useAppStore.getState().rideMetricsHistory[0].feelLegs).toBe('heavy'),
    )
  })

  it('forwards the external id so intervals rides resolve server-side (#441)', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({
          stravaActivityId: 557,
          externalActivityId: 'i84213307',
          activityDate: yesterday,
        }),
      ],
    })
    renderDashboard()

    const heavy = await screen.findByRole('button', { name: 'Legs felt Heavy' })
    fireEvent.click(heavy)

    expect(mockSetRideLegs).toHaveBeenCalledWith('test-token', 557, 'heavy', 'i84213307')
  })

  it('clears the rating when the active feel is tapped again', async () => {
    setupStore({
      rideMetricsHistory: [
        makeRide({ stravaActivityId: 556, activityDate: yesterday, feelLegs: 'fresh' }),
      ],
    })
    renderDashboard()

    const fresh = await screen.findByRole('button', { name: 'Legs felt Fresh' })
    fireEvent.click(fresh)

    expect(mockSetRideLegs).toHaveBeenCalledWith('test-token', 556, null, undefined)
    await waitFor(() =>
      expect(useAppStore.getState().rideMetricsHistory[0].feelLegs).toBeNull(),
    )
  })

  it('reverts the optimistic update when the request fails', async () => {
    mockSetRideLegs.mockRejectedValueOnce(new Error('network'))
    setupStore({
      rideMetricsHistory: [makeRide({ stravaActivityId: 557, activityDate: yesterday })],
    })
    renderDashboard()

    const normal = await screen.findByRole('button', { name: 'Legs felt Normal' })
    fireEvent.click(normal)

    await waitFor(() =>
      expect(useAppStore.getState().rideMetricsHistory[0].feelLegs ?? null).toBeNull(),
    )
  })
})

// ---------------------------------------------------------------------------
// Strava analysis progress
// ---------------------------------------------------------------------------

describe('DashboardPage — Strava analysis progress', () => {
  it('shows the analysis progress bar while an import is running', async () => {
    mockUseImportProgress.mockReturnValue({
      ...idleImportProgress,
      status: 'running',
      total: 10,
      processed: 4,
    })
    setupStore({ stravaConnection })
    renderDashboard()

    expect(await screen.findByText('Strava Activity Analysis')).toBeInTheDocument()
    expect(screen.getByText('4 / 10 activities analyzed')).toBeInTheDocument()
  })

  it('surfaces the error message when the import fails', async () => {
    mockUseImportProgress.mockReturnValue({
      ...idleImportProgress,
      status: 'error',
      total: 10,
      processed: 3,
      error: 'Strava rate limit exceeded',
    })
    setupStore({ stravaConnection })
    renderDashboard()

    expect(await screen.findByText('Strava rate limit exceeded')).toBeInTheDocument()
    expect(screen.getByText('3 / 10 activities analyzed')).toBeInTheDocument()
  })

  it('does not show the progress bar when Strava is not connected', async () => {
    mockUseImportProgress.mockReturnValue({
      ...idleImportProgress,
      status: 'running',
      total: 10,
      processed: 4,
    })
    setupStore({ stravaConnection: null })
    renderDashboard()

    await waitFor(() => {
      expect(screen.queryByText('Strava Activity Analysis')).not.toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Login summary loading state
// ---------------------------------------------------------------------------

describe('DashboardPage — login summary loading', () => {
  it('shows the loading placeholder while the summary is being prepared', async () => {
    // A never-resolving refresh keeps summaryLoading true so the placeholder renders.
    mockRefreshLoginSummary.mockReturnValue(new Promise(() => {}))
    setupStore({
      riderAssessment: { riderType: 'allrounder', notes: '', loginSummary: '' },
    })
    renderDashboard()

    expect(await screen.findByText('Preparing your training summary…')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Today's status strip (#417)
// ---------------------------------------------------------------------------

describe("DashboardPage — Today's status strip", () => {
  const planDay = (overrides: Partial<TrainingDay> & { date: string }): TrainingDay => ({
    workoutType: 'endurance',
    title: 'Session',
    description: '',
    durationMinutes: 60,
    ...overrides,
  })

  // Today and tomorrow moved out of this grey line: today into the hero card,
  // the rest of the week into WeekStrip. The assertions follow them.
  it("shows today's planned session when nothing is logged yet", async () => {
    setupStore({
      trainingPlan: [planDay({ date: today, workoutType: 'tempo', title: 'Sweet Spot' })],
    })
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'Sweet Spot' })).toBeInTheDocument()
  })

  it('shows the rest day as the session for today', async () => {
    setupStore({
      trainingPlan: [planDay({ date: today, workoutType: 'rest', title: 'Rest Day' })],
    })
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'Rest Day' })).toBeInTheDocument()
  })

  it("marks today's session done once an activity is logged, even unticked", async () => {
    setupStore({
      trainingPlan: [planDay({ date: today, workoutType: 'intervals', title: 'VO2 Efforts' })],
      rideMetricsHistory: [makeRide({ activityDate: today, activityName: 'Morning Intervals' })],
    })
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'VO2 Efforts' })).toBeInTheDocument()
    expect(screen.getByText('Done')).toBeInTheDocument()
  })

  it('shows the rest of the week as its own days', async () => {
    setupStore({
      trainingPlan: [planDay({ date: tomorrow, workoutType: 'rest', title: 'Rest Day' })],
    })
    renderDashboard()
    expect(await screen.findByText('This week')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: weekdayOf(tomorrow, 'Rest') }))

    expect(await screen.findByRole('heading', { name: 'Rest Day' })).toBeInTheDocument()
  })

  // The class #648 is about: every view that turns a plan into "the day" has
  // dropped the second session at least once. This is the integration point.
  it('shows both sessions of a two-a-day, and marks the week strip', async () => {
    setupStore({
      trainingPlan: [
        planDay({ date: today, slot: 0, workoutType: 'intervals', title: 'Morning intervals' }),
        planDay({ date: today, slot: 1, workoutType: 'recovery', title: 'Evening spin' }),
      ],
    })
    renderDashboard()

    expect(await screen.findByRole('heading', { name: 'Morning intervals' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evening spin' })).toBeInTheDocument()
    expect(screen.getByText('2 sessions')).toBeInTheDocument()
  })

  it('leaves the dashboard alone when a day is picked', async () => {
    setupStore({
      trainingPlan: [
        planDay({ date: today, workoutType: 'intervals', title: 'VO2 Efforts' }),
        planDay({ date: tomorrow, workoutType: 'rest', title: 'Rest Day' }),
      ],
    })
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'VO2 Efforts' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: weekdayOf(tomorrow, 'Rest') }))

    // The hero swapped; the page around it did not go anywhere (#634).
    expect(await screen.findByRole('heading', { name: 'Rest Day' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'VO2 Efforts' })).not.toBeInTheDocument()
    expect(screen.getByText('This week')).toBeInTheDocument()
    expect(screen.getByTestId('ai-chat')).toBeInTheDocument()
  })

  it('renders the coach-authored status badge verbatim', async () => {
    setupStore({
      trainingPlan: [planDay({ date: yesterday, workoutType: 'endurance' })],
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'Ahead of plan',
        trainingStatusTone: 'positive',
      }),
    })
    renderDashboard()
    expect(await screen.findByText('Ahead of plan')).toBeInTheDocument()
  })

  // The verdict is the summary card's colour now (#623), so these read the card
  // rather than a pill. `summaryCard()` walks up from the section heading to the
  // bordered block that carries the tone.
  const summaryCard = () =>
    screen.getByText('Your recent training summary').closest('div.rounded-xl')!

  it('colours the whole summary from the coach-chosen tone', async () => {
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'Missed two',
        trainingStatusTone: 'caution',
      }),
    })
    renderDashboard()
    await screen.findByText('Your recent training summary')

    expect(summaryCard()).toHaveClass('bg-amber-50', 'border-amber-200')
  })

  it('reserves red for a block that stopped happening, not a missed session', async () => {
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'Behind plan',
        trainingStatusTone: 'alert',
      }),
    })
    renderDashboard()
    await screen.findByText('Your recent training summary')

    expect(summaryCard()).toHaveClass('bg-red-50', 'border-red-200')
  })

  it('goes green when the athlete is meeting the plan', async () => {
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'On track',
        trainingStatusTone: 'positive',
      }),
    })
    renderDashboard()
    await screen.findByText('Your recent training summary')

    expect(summaryCard()).toHaveClass('bg-emerald-50', 'border-emerald-200')
  })

  it('falls back to a neutral card when the stored tone is unusable', async () => {
    // `training_status_tone` is a free-text column, so a legacy or malformed
    // value must still render — just without claiming a verdict it cannot back.
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'Easing off',
        trainingStatusTone: undefined,
      }),
    })
    renderDashboard()
    await screen.findByText('Your recent training summary')

    expect(summaryCard()).toHaveClass('bg-white', 'border-gray-100')
  })

  // Colour alone reaches nobody who cannot see it, so the coach's word stays in
  // the DOM even though it is no longer drawn.
  it('keeps the verdict readable to a screen reader', async () => {
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'Missed two',
        trainingStatusTone: 'caution',
      }),
    })
    renderDashboard()

    expect(await screen.findByText('Missed two')).toHaveClass('sr-only')
  })

  it("exposes the coach's reason as the summary's tooltip", async () => {
    // The athlete asks "why?" of the colour; the same rationale also goes into
    // the coach's prompt, so both answers come from one source (#499).
    setupStore({
      riderAssessment: assessmentWithStatus({
        trainingStatusLabel: 'On track',
        trainingStatusRationale: 'You completed both hard sessions this week.',
      }),
    })
    renderDashboard()
    await screen.findByText('Your recent training summary')

    expect(summaryCard()).toHaveAttribute(
      'title',
      'You completed both hard sessions this week.'
    )
  })

  it('never derives a status from plan adherence in the browser', async () => {
    // The old local `done / due` heuristic produced "Slightly behind" from exactly
    // this data while the coach knew nothing about it (#499). With no stored badge
    // the segment must simply be absent rather than locally invented.
    setupStore({
      trainingPlan: [
        planDay({ date: yesterday, workoutType: 'endurance', completed: true }),
        planDay({ date: twoDaysAgo, workoutType: 'intervals', completed: false }),
      ],
      riderAssessment: assessmentWithStatus({ trainingStatusLabel: undefined }),
    })
    renderDashboard()
    expect(screen.queryByText('Slightly behind')).not.toBeInTheDocument()
    expect(screen.queryByText('On track')).not.toBeInTheDocument()
    expect(screen.queryByText('Behind plan')).not.toBeInTheDocument()
  })

  it('asks the backend for a badge when none is stored', async () => {
    mockRefreshTrainingStatus.mockResolvedValue({
      label: 'On track',
      tone: 'positive',
      rationale: 'Everything the plan asked for is done.',
    })
    setupStore({
      trainingPlan: [planDay({ date: yesterday, workoutType: 'endurance' })],
      riderAssessment: assessmentWithStatus({ trainingStatusLabel: undefined }),
    })
    renderDashboard()
    expect(await screen.findByText('On track')).toBeInTheDocument()
    expect(mockRefreshTrainingStatus).toHaveBeenCalledWith('test-token')
  })

  it('does not re-request a badge that is already stored', async () => {
    setupStore({
      riderAssessment: assessmentWithStatus({ trainingStatusLabel: 'On track' }),
    })
    renderDashboard()
    expect(await screen.findByText('On track')).toBeInTheDocument()
    expect(mockRefreshTrainingStatus).not.toHaveBeenCalled()
  })

  it('omits the training-status segment when the athlete has no assessment', async () => {
    setupStore({
      trainingPlan: [
        planDay({ date: today, workoutType: 'endurance', title: 'Base Ride' }),
        planDay({ date: yesterday, workoutType: 'rest', title: 'Rest Day' }),
      ],
    })
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'Base Ride' })).toBeInTheDocument()
    expect(screen.queryByText('On track')).not.toBeInTheDocument()
    expect(screen.queryByText('Slightly behind')).not.toBeInTheDocument()
    expect(screen.queryByText('Behind plan')).not.toBeInTheDocument()
  })

  it('renders no status strip when there is no plan', async () => {
    setupStore({ trainingPlan: [] })
    renderDashboard()
    // Dashboard still mounts…
    expect(await screen.findByTestId('ai-chat')).toBeInTheDocument()
    // …but none of the strip's segments appear.
    expect(screen.queryByText(/^Today:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Tomorrow:/)).not.toBeInTheDocument()
    expect(screen.queryByText('On track')).not.toBeInTheDocument()
  })
})
