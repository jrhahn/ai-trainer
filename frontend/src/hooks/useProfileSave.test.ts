import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useNote } from './useProfileSave'

describe('useNote (ai-trainer-ops#274)', () => {
  afterEach(() => vi.useRealTimers())

  it('hides a note after its time, and a newer note keeps its own time', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useNote())

    act(() => result.current[1]({ type: 'success', text: 'Automatic Strava sync enabled.' }, 3000))
    act(() => vi.advanceTimersByTime(2000))
    act(() => result.current[1]({ type: 'success', text: 'Automatic Strava sync disabled.' }, 3000))
    act(() => vi.advanceTimersByTime(2000))
    expect(result.current[0]?.text).toBe('Automatic Strava sync disabled.')

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current[0]).toBeNull()
  })

  it('keeps an error until the next note, and clears its timer with the card', () => {
    vi.useFakeTimers()
    const clear = vi.spyOn(window, 'clearTimeout')
    const { result, unmount } = renderHook(() => useNote())

    act(() => result.current[1]({ type: 'error', text: 'Could not save Strava sync setting. Please try again.' }))
    act(() => vi.advanceTimersByTime(60_000))
    expect(result.current[0]?.type).toBe('error')

    act(() => result.current[1]({ type: 'success', text: 'Saved' }, 3000))
    clear.mockClear()
    unmount()
    expect(clear).toHaveBeenCalled()
  })
})
