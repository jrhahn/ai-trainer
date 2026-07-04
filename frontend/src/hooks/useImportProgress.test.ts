import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAppStore } from '../store/useAppStore'
import { useImportProgress, type ImportProgress } from './useImportProgress'

const { mockGetStravaImportProgress } = vi.hoisted(() => ({
  mockGetStravaImportProgress: vi.fn(),
}))

vi.mock('../services/strava', () => ({
  getStravaImportProgress: mockGetStravaImportProgress,
}))

const POLL_MS = 3000

const RUNNING: ImportProgress = {
  status: 'running',
  total: 10,
  processed: 5,
  imported: 5,
  skipped: 0,
  failedActivities: [],
  error: '',
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  mockGetStravaImportProgress.mockResolvedValue(RUNNING)
  useAppStore.setState({ authToken: 'tok' })
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

async function flush() {
  // Advance fake timers and drain the async pollOnce microtasks.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS)
  })
}

describe('useImportProgress', () => {
  it('keeps polling for remaining consumers when one polling consumer unmounts (#328)', async () => {
    const a = renderHook(() => useImportProgress({ poll: true }))
    const b = renderHook(() => useImportProgress({ poll: true }))
    await flush()

    // One polling consumer leaves; another still wants live updates.
    a.unmount()

    const callsBefore = mockGetStravaImportProgress.mock.calls.length
    await flush()

    expect(mockGetStravaImportProgress.mock.calls.length).toBeGreaterThan(callsBefore)

    b.unmount()
  })

  it('stops polling only once the last polling consumer unmounts', async () => {
    const poller = renderHook(() => useImportProgress({ poll: true }))
    // A passive listener that never requested polling.
    const viewer = renderHook(() => useImportProgress())
    await flush()

    poller.unmount()

    const callsBefore = mockGetStravaImportProgress.mock.calls.length
    await flush()

    // No polling consumers remain, so no further requests are made.
    expect(mockGetStravaImportProgress.mock.calls.length).toBe(callsBefore)

    viewer.unmount()
  })

  it('does not poll at all for a non-polling consumer', async () => {
    const viewer = renderHook(() => useImportProgress())
    await flush()

    expect(mockGetStravaImportProgress).not.toHaveBeenCalled()

    viewer.unmount()
  })
})
