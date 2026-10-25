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

  it('shows a passive viewer, and one mounted later, what the poller fetched', async () => {
    const poller = renderHook(() => useImportProgress({ poll: true }))
    const viewer = renderHook(() => useImportProgress())
    await flush()

    expect(viewer.result.current).toEqual(RUNNING)
    const late = renderHook(() => useImportProgress())
    expect(late.result.current).toEqual(RUNNING)

    late.unmount()
    viewer.unmount()
    poller.unmount()
  })

  it('shows nothing once signed out, and the next sign-in starts clean', async () => {
    const poller = renderHook(() => useImportProgress({ poll: true }))
    await flush()
    expect(poller.result.current.status).toBe('running')

    act(() => useAppStore.setState({ authToken: null }))
    expect(poller.result.current.status).toBe('idle')

    mockGetStravaImportProgress.mockClear()
    act(() => useAppStore.setState({ authToken: 'tok-2' }))
    expect(poller.result.current.status).toBe('idle')
    await flush()
    expect(mockGetStravaImportProgress).toHaveBeenCalledWith('tok-2')
    expect(poller.result.current.status).toBe('running')

    poller.unmount()
  })

  it('keeps the result when its only consumer stops polling', async () => {
    // The Strava callback page polls while importing and stops once done.
    let poll = true
    const page = renderHook(() => useImportProgress({ poll }))
    await flush()
    expect(page.result.current).toEqual(RUNNING)

    poll = false
    page.rerender()
    expect(page.result.current).toEqual(RUNNING)

    page.unmount()
  })

  it('does not poll at all for a non-polling consumer', async () => {
    const viewer = renderHook(() => useImportProgress())
    await flush()

    expect(mockGetStravaImportProgress).not.toHaveBeenCalled()

    viewer.unmount()
  })
})
