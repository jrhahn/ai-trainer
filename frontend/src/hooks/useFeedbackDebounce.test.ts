import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useAppStore } from '../store/useAppStore'
import { useFeedbackDebounce } from './useFeedbackDebounce'

const { mockProcessPendingFeedbacks, mockFetchRideMetricsHistory } = vi.hoisted(() => ({
  mockProcessPendingFeedbacks: vi.fn(),
  mockFetchRideMetricsHistory: vi.fn(),
}))

vi.mock('../services/ai', () => ({ processPendingFeedbacks: mockProcessPendingFeedbacks }))
vi.mock('../services/user', () => ({ fetchRideMetricsHistory: mockFetchRideMetricsHistory }))

const DEBOUNCE_MS = 60_000

beforeEach(() => {
  vi.useFakeTimers()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok-123' })
  vi.clearAllMocks()
  mockProcessPendingFeedbacks.mockResolvedValue('Updated login summary')
  mockFetchRideMetricsHistory.mockResolvedValue([
    { recordedAt: '2024-05-01', source: 'recalc' },
  ])
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useFeedbackDebounce', () => {
  it('reports isPending based on the number of pending rides', () => {
    const { result, rerender } = renderHook(() => useFeedbackDebounce())
    expect(result.current.isPending).toBe(false)

    act(() => {
      useAppStore.getState().addPendingFeedbackRide(1)
    })
    rerender()
    expect(result.current.isPending).toBe(true)
  })

  it('flushes pending feedback after the debounce window', async () => {
    renderHook(() => useFeedbackDebounce())

    act(() => {
      useAppStore.getState().addPendingFeedbackRide(1)
      useAppStore.getState().addPendingFeedbackRide(2)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS)
    })

    expect(mockProcessPendingFeedbacks).toHaveBeenCalledTimes(1)
    expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('tok-123', [1, 2])
    expect(mockFetchRideMetricsHistory).toHaveBeenCalledWith('tok-123')
    expect(useAppStore.getState().riderAssessment?.loginSummary).toBe('Updated login summary')
    expect(useAppStore.getState().pendingFeedbackRideIds).toEqual([])
  })

  it('does not fire before the debounce window elapses', async () => {
    renderHook(() => useFeedbackDebounce())

    act(() => {
      useAppStore.getState().addPendingFeedbackRide(1)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 1)
    })

    expect(mockProcessPendingFeedbacks).not.toHaveBeenCalled()
  })

  it('resets the timer when more rides are added', async () => {
    renderHook(() => useFeedbackDebounce())

    act(() => {
      useAppStore.getState().addPendingFeedbackRide(1)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 5_000)
    })
    // Adding a ride resets the debounce timer
    act(() => {
      useAppStore.getState().addPendingFeedbackRide(2)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 5_000)
    })
    expect(mockProcessPendingFeedbacks).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })
    expect(mockProcessPendingFeedbacks).toHaveBeenCalledWith('tok-123', [1, 2])
  })

  it('does not call the backend when there is no auth token', async () => {
    useAppStore.setState({ authToken: null })
    renderHook(() => useFeedbackDebounce())

    act(() => {
      useAppStore.getState().addPendingFeedbackRide(1)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEBOUNCE_MS)
    })

    expect(mockProcessPendingFeedbacks).not.toHaveBeenCalled()
  })
})
