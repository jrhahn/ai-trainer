import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import DashboardPage from './DashboardPage'
import { computeMatchScore, matchScoreLabel } from './DashboardPage'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { formatLocalDate } from '../utils/workout'

// ---------------------------------------------------------------------------
// Hoisted mocks
// ---------------------------------------------------------------------------

const { mockAdaptTrainingPlan, mockRefreshLoginSummary } = vi.hoisted(() => ({
  mockAdaptTrainingPlan: vi.fn(),
  mockRefreshLoginSummary: vi.fn(),
}))

vi.mock('../services/ai', () => ({
  adaptTrainingPlan: mockAdaptTrainingPlan,
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
    const tomorrow = formatLocalDate(new Date(Date.now() + 1 * 24 * 60 * 60 * 1000))
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
    const tomorrow = formatLocalDate(new Date(Date.now() + 1 * 24 * 60 * 60 * 1000))
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

  it('does not show plan row for an unmatched ride', async () => {
    const unmatchedRide = makeRide({
      activityDate: yesterday,
      activityName: 'Free Ride',
      planMatchStatus: 'unmatched',
      matchedPlanSnapshot: null,
    })
    setupStore({ rideMetricsHistory: [unmatchedRide] })
    renderDashboard()

    expect(await screen.findByText('Free Ride')).toBeInTheDocument()
    expect(screen.queryByText('planned:')).not.toBeInTheDocument()
  })

  it('shows what was planned when an unmatched ride still has a plan snapshot', async () => {
    const restDayRide = makeRide({
      activityDate: yesterday,
      activityName: 'Bonus Spin',
      planMatchStatus: 'unmatched',
      matchedPlanDate: yesterday,
      matchedPlanSnapshot: {
        title: 'Rest Day',
        workoutType: 'rest',
        durationMinutes: 0,
      },
    })
    setupStore({ rideMetricsHistory: [restDayRide] })
    renderDashboard()

    expect(await screen.findByText('planned:')).toBeInTheDocument()
    expect(screen.getByText(/Rest Day/)).toBeInTheDocument()
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
})
