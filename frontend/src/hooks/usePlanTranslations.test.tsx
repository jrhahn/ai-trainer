import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { usePlanText, usePlanTranslationSync } from './usePlanTranslations'
import { useAppStore, type TrainingDay } from '../store/useAppStore'

const mockFetch = vi.hoisted(() => vi.fn())
const mockWrite = vi.hoisted(() => vi.fn())

vi.mock('../services/ai', () => ({
  fetchPlanTranslations: mockFetch,
  writePlanTranslations: mockWrite,
}))

const day = (title: string, overrides: Partial<TrainingDay> = {}): TrainingDay => ({
  date: '2026-10-18',
  workoutType: 'endurance',
  title,
  description: 'Steady Z2.',
  durationMinutes: 60,
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'token', uiLanguage: 'de', trainingPlan: [day('Endurance Ride')] })
})

afterEach(() => {
  useAppStore.setState({ uiLanguage: 'auto' })
})

describe('usePlanTranslationSync', () => {
  it('reads the stored translations and asks once for the missing ones', async () => {
    mockFetch.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: true })
    mockWrite.mockResolvedValue({
      language: 'de',
      translations: { 'Endurance Ride': 'Grundlagenfahrt', 'Steady Z2.': 'Gleichmäßig in Z2.' },
      missing: false,
    })

    const { rerender } = renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(useAppStore.getState().planTranslations['Steady Z2.']).toBe('Gleichmäßig in Z2.'))
    expect(mockFetch).toHaveBeenCalledWith('token', 'de')
    expect(mockWrite).toHaveBeenCalledWith('token', 'de')

    // The same texts are not asked for again, even when one is still owed:
    // a fresh copy of the plan with the same texts changes nothing.
    mockFetch.mockClear()
    mockWrite.mockClear()
    act(() => useAppStore.setState({ trainingPlan: [day('Endurance Ride')] }))
    rerender()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()

    // Switching away and back reads them again, but writes nothing new.
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    act(() => useAppStore.setState({ uiLanguage: 'de' }))
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockWrite).not.toHaveBeenCalled()

    // A rewritten text is asked for.
    act(() => useAppStore.setState({ trainingPlan: [day('Recovery Spin')] }))
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))
  })

  it('writes nothing when every text is translated', async () => {
    mockFetch.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false })

    renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(useAppStore.getState().planTranslations).toEqual({ 'Endurance Ride': 'Grundlagenfahrt' }))
    expect(mockWrite).not.toHaveBeenCalled()
  })

  it('asks nothing for an English interface, a signed-out browser or an empty plan', async () => {
    useAppStore.setState({ uiLanguage: 'en' })
    const { unmount } = renderHook(() => usePlanTranslationSync())
    unmount()
    useAppStore.setState({ uiLanguage: 'de', authToken: null })
    renderHook(() => usePlanTranslationSync()).unmount()
    useAppStore.setState({ authToken: 'token', trainingPlan: [] })
    renderHook(() => usePlanTranslationSync()).unmount()

    await Promise.resolve()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('keeps the English texts when the server answers in another language or fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockFetch.mockResolvedValueOnce({ language: null, translations: {}, missing: false })
    const first = renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    first.unmount()

    mockFetch.mockResolvedValueOnce({ language: 'de', translations: {}, missing: true })
    mockWrite.mockRejectedValueOnce(new Error('The AI service is temporarily unavailable'))
    renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(warn).toHaveBeenCalledWith(
      '[PlanTranslations] could not load the plan in', 'de', expect.any(Error),
    ))
    expect(useAppStore.getState().planTranslations).toEqual({})
    warn.mockRestore()
  })
})

describe('usePlanText', () => {
  it('shows the translation in a German interface and the English text otherwise', () => {
    useAppStore.setState({ planTranslations: { 'Endurance Ride': 'Grundlagenfahrt' } })
    const { result, rerender } = renderHook(() => usePlanText())

    expect(result.current(' Endurance Ride ')).toBe('Grundlagenfahrt')
    expect(result.current('Untranslated')).toBe('Untranslated')
    expect(result.current(undefined)).toBeUndefined()
    expect(result.current('')).toBe('')

    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    rerender()
    expect(result.current('Endurance Ride')).toBe('Endurance Ride')
  })
})
