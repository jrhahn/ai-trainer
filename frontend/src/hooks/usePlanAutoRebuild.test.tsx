import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { REBUILD_SETTLE_MS, usePlanAutoRebuild } from './usePlanAutoRebuild'
import { useAppStore } from '../store/useAppStore'
import type { RiderAssessment, TrainingDay, UserProfile } from '../store/useAppStore'
import { getPlan } from '../queries/plan'
import { setProfile } from '../queries/profile'

const { mockChanged, mockRebuild, mockSummary } = vi.hoisted(() => ({
  mockChanged: vi.fn(),
  mockRebuild: vi.fn(),
  mockSummary: vi.fn(),
}))
vi.mock('../services/ai', () => ({
  fetchPlanInputsChanged: mockChanged,
  rebuildPlanIfChanged: mockRebuild,
  refreshLoginSummary: mockSummary,
}))

const PROFILE = {
  name: 'Mara',
  email: 'mara@example.com',
  trainingGoal: 'general_fitness',
  fitnessLevel: 'intermediate',
  sports: ['cycling'],
  trainingDays: ['tuesday', 'thursday'],
} as UserProfile
const NEW_PLAN = [{ date: '2026-11-02', workoutType: 'endurance', title: 'Rebuilt' }] as TrainingDay[]

function signIn(profile: Partial<UserProfile> = {}) {
  setProfile({ ...PROFILE, ...profile })
  useAppStore.setState({ authToken: 'tok', isOnboarded: true })
}

/** Let the timers and the promises behind them run. */
async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockChanged.mockResolvedValue({ changed: false, reasons: [], rebuildsLeft: 6 })
  mockRebuild.mockResolvedValue({ rebuilt: true, reasons: ['training_days'], plan: NEW_PLAN })
  mockSummary.mockResolvedValue('This week: two rides, Monday and Thursday.')
})

afterEach(() => {
  vi.useRealTimers()
})

describe('usePlanAutoRebuild (ai-trainer-ops#206)', () => {
  it('fetches the summary of the new week after a rebuild, not the old one (ai-trainer-ops#273)', async () => {
    const assessment = { loginSummary: 'This week: two rides, Tuesday and Thursday.' } as RiderAssessment
    signIn()
    useAppStore.setState({ riderAssessment: assessment })
    renderHook(() => usePlanAutoRebuild())
    await settle()

    mockChanged.mockResolvedValue({ changed: true, reasons: ['training_days'], rebuildsLeft: 6 })
    act(() => signIn({ trainingDays: ['monday', 'thursday'] }))
    await settle(REBUILD_SETTLE_MS)

    expect(mockSummary).toHaveBeenCalledWith('tok')
    expect(useAppStore.getState().riderAssessment?.loginSummary).toBe('This week: two rides, Monday and Thursday.')
  })

  it('asks for no summary before the first assessment, or without a rebuild', async () => {
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    mockChanged.mockResolvedValue({ changed: true, reasons: ['training_days'], rebuildsLeft: 6 })
    act(() => signIn({ trainingDays: ['monday', 'thursday'] }))
    await settle(REBUILD_SETTLE_MS)
    expect(mockRebuild).toHaveBeenCalledTimes(1)

    useAppStore.setState({ riderAssessment: { loginSummary: 'Old.' } as RiderAssessment })
    mockRebuild.mockResolvedValue({ rebuilt: false, reasons: [], plan: null })
    act(() => signIn({ trainingDays: ['monday', 'friday'] }))
    await settle(REBUILD_SETTLE_MS)
    expect(mockRebuild).toHaveBeenCalledTimes(2)

    expect(mockSummary).not.toHaveBeenCalled()
  })

  it('rebuilds after the athlete changes the training days, once things settle', async () => {
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    expect(mockRebuild).not.toHaveBeenCalled()

    mockChanged.mockResolvedValue({ changed: true, reasons: ['training_days'], rebuildsLeft: 6 })
    act(() => signIn({ trainingDays: ['monday', 'thursday'] }))
    await settle(REBUILD_SETTLE_MS - 100)
    expect(mockRebuild).not.toHaveBeenCalled()
    await settle(200)

    expect(mockRebuild).toHaveBeenCalledTimes(1)
    expect(getPlan()).toEqual(NEW_PLAN)
    expect(useAppStore.getState().planRebuild?.reasons).toEqual(['training_days'])
  })

  it('gives several changes in a row one rebuild', async () => {
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    mockChanged.mockResolvedValue({ changed: true, reasons: ['sports', 'level'], rebuildsLeft: 6 })

    act(() => signIn({ sports: ['cycling', 'running'] }))
    await settle(500)
    act(() => signIn({ sports: ['cycling', 'running'], fitnessLevel: 'beginner' }))
    await settle(500)
    act(() => signIn({ sports: ['cycling', 'running'], fitnessLevel: 'beginner', trainingDays: ['monday'] }))
    await settle(REBUILD_SETTLE_MS)

    expect(mockRebuild).toHaveBeenCalledTimes(1)
  })

  it('rebuilds on load for a change saved in an earlier visit', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['race'], rebuildsLeft: 6 })
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()

    expect(mockRebuild).toHaveBeenCalledTimes(1)
  })

  it('leaves new rides and answered questions to the nightly run', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['history', 'assumptions'], rebuildsLeft: 6 })
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    act(() => signIn({ currentFTP: 250 }))
    await settle(REBUILD_SETTLE_MS)

    expect(mockRebuild).not.toHaveBeenCalled()
  })

  it('keeps the plan when the server rebuilt nothing, or failed', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['ftp'], rebuildsLeft: 0 })
    mockRebuild.mockResolvedValueOnce({ rebuilt: false, reasons: ['ftp'], limited: true })
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    expect(useAppStore.getState().planRebuild).toBeNull()

    expect(useAppStore.getState().notice).toBeNull()

    mockRebuild.mockRejectedValueOnce(new Error('offline'))
    act(() => signIn({ currentFTP: 260 }))
    await settle(REBUILD_SETTLE_MS)
    expect(useAppStore.getState().planRebuild).toBeNull()
    expect(getPlan()).toEqual([])
    // Said, not swallowed (ai-trainer-ops#272).
    expect(useAppStore.getState().notice).toBe(
      'Your plan could not be rebuilt after your change. It is rebuilt tonight.',
    )
  })

  it('asks once more for a change saved while a rebuild was on its way', async () => {
    let finish: (value: unknown) => void = () => {}
    mockChanged.mockResolvedValue({ changed: true, reasons: ['sports'], rebuildsLeft: 6 })
    mockRebuild.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    signIn()
    renderHook(() => usePlanAutoRebuild())
    await settle()
    expect(mockRebuild).toHaveBeenCalledTimes(1)

    act(() => signIn({ fitnessLevel: 'advanced' }))
    await settle(REBUILD_SETTLE_MS)
    expect(mockRebuild).toHaveBeenCalledTimes(1)

    await act(async () => finish({ rebuilt: true, reasons: ['sports'], plan: NEW_PLAN }))
    await settle()
    expect(mockRebuild).toHaveBeenCalledTimes(2)
  })

  it('does nothing before onboarding or without a session', async () => {
    mockChanged.mockResolvedValue({ changed: true, reasons: ['race'], rebuildsLeft: 6 })
    setProfile(PROFILE)
    useAppStore.setState({ authToken: 'tok', isOnboarded: false })
    const { unmount } = renderHook(() => usePlanAutoRebuild())
    await settle()
    unmount()
    useAppStore.setState({ authToken: null, isOnboarded: true })
    renderHook(() => usePlanAutoRebuild())
    await settle()

    expect(mockChanged).not.toHaveBeenCalled()
    expect(mockRebuild).not.toHaveBeenCalled()
  })
})
