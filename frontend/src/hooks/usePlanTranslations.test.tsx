import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { MAX_WRITE_ROUNDS, usePlanText, usePlanTextPending, usePlanTranslationSync } from './usePlanTranslations'
import { useAppStore, type TrainingDay } from '../store/useAppStore'
import { setPlan } from '../queries/plan'

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
  setPlan([day('Endurance Ride')])
  useAppStore.setState({ authToken: 'token', uiLanguage: 'de' })
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
    act(() => setPlan([day('Endurance Ride')]))
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
    act(() => setPlan([day('Recovery Spin')]))
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))
  })

  it('writes nothing when every text is translated', async () => {
    mockFetch.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false })

    renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(useAppStore.getState().planTranslations).toEqual({ 'Endurance Ride': 'Grundlagenfahrt' }))
    expect(mockWrite).not.toHaveBeenCalled()
  })

  it('writes in rounds while texts are missing, and stops when a round adds nothing', async () => {
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite
      .mockResolvedValueOnce({ language: 'de', translations: { a: 'A' }, missing: true })
      .mockResolvedValueOnce({ language: 'de', translations: { a: 'A', b: 'B' }, missing: true })
      .mockResolvedValueOnce({ language: 'de', translations: { a: 'A', b: 'B' }, missing: true })

    renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(3))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockWrite).toHaveBeenCalledTimes(3)
    expect(useAppStore.getState().planTranslations).toEqual({ a: 'A', b: 'B' })
  })

  it(`stops after ${MAX_WRITE_ROUNDS} rounds`, async () => {
    let n = 0
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite.mockImplementation(async () => {
      n += 1
      return { language: 'de', translations: Object.fromEntries(Array.from({ length: n }, (_, i) => [`t${i}`, `T${i}`])), missing: true }
    })

    renderHook(() => usePlanTranslationSync())

    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(MAX_WRITE_ROUNDS))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockWrite).toHaveBeenCalledTimes(MAX_WRITE_ROUNDS)
  })

  it('keeps what an overtaken run was given, and the run that replaced it still writes', async () => {
    let finishFirstWrite: (value: unknown) => void = () => {}
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirstWrite = resolve }))
      .mockResolvedValueOnce({ language: 'de', translations: { 'Steady Z2.': 'Gleichmäßig in Z2.' }, missing: false })

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))

    // The effect runs again for the same plan while the first write is out.
    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    act(() => useAppStore.setState({ uiLanguage: 'de' }))
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(2))

    await act(async () => finishFirstWrite({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false }))

    await waitFor(() => expect(useAppStore.getState().planTranslations).toEqual({
      'Endurance Ride': 'Grundlagenfahrt',
      'Steady Z2.': 'Gleichmäßig in Z2.',
    }))
  })

  it('lets the next run write when a torn-down run fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    let failFirstRead: (error: Error) => void = () => {}
    mockFetch
      .mockImplementationOnce(() => new Promise((_, reject) => { failFirstRead = reject }))
      .mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false })

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    await act(async () => failFirstRead(new Error('aborted')))
    act(() => useAppStore.setState({ uiLanguage: 'de' }))

    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))
    warn.mockRestore()
  })

  it('drops what a run was given once the interface left its language', async () => {
    let finishWrite: (value: unknown) => void = () => {}
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite.mockImplementationOnce(() => new Promise((resolve) => { finishWrite = resolve }))

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))
    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    await act(async () => finishWrite({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false }))

    expect(useAppStore.getState().planTranslations).toEqual({})
  })

  it('asks nothing for an English interface, a signed-out browser or an empty plan', async () => {
    useAppStore.setState({ uiLanguage: 'en' })
    const { unmount } = renderHook(() => usePlanTranslationSync())
    unmount()
    useAppStore.setState({ uiLanguage: 'de', authToken: null })
    renderHook(() => usePlanTranslationSync()).unmount()
    setPlan([])
    useAppStore.setState({ authToken: 'token' })
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

describe('the texts on their way (ai-trainer-ops#271)', () => {
  const pending = () => useAppStore.getState().planTranslationsPending

  it('says so while it writes, and stops when the writes are over', async () => {
    let finishWrite: (value: unknown) => void = () => {}
    mockFetch.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: true })
    mockWrite.mockImplementationOnce(() => new Promise((resolve) => { finishWrite = resolve }))

    renderHook(() => usePlanTranslationSync())
    const { result } = renderHook(() => usePlanTextPending())

    await waitFor(() => expect(pending()).toBe(true))
    expect(result.current('Steady Z2.')).toBe(true)
    // A text that has its German already is not waiting for it.
    expect(result.current('Endurance Ride')).toBe(false)

    await act(async () => finishWrite({
      language: 'de',
      translations: { 'Endurance Ride': 'Grundlagenfahrt', 'Steady Z2.': 'Gleichmäßig in Z2.' },
      missing: false,
    }))

    expect(pending()).toBe(false)
    expect(result.current('Steady Z2.')).toBe(false)
  })

  it('stops saying so when a write fails, and the English stays', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    let failWrite: (error: Error) => void = () => {}
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite.mockImplementationOnce(() => new Promise((_, reject) => { failWrite = reject }))

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(pending()).toBe(true))
    await act(async () => failWrite(new Error('The AI service is temporarily unavailable')))

    expect(pending()).toBe(false)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('keeps saying so until the last of two overlapping runs is done', async () => {
    const finish: ((value: unknown) => void)[] = []
    mockFetch.mockResolvedValue({ language: 'de', translations: {}, missing: true })
    mockWrite.mockImplementation(() => new Promise((resolve) => { finish.push(resolve) }))

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(1))
    // The effect runs again for the same plan while the first write is out.
    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    act(() => useAppStore.setState({ uiLanguage: 'de' }))
    await waitFor(() => expect(mockWrite).toHaveBeenCalledTimes(2))

    const done = { language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false }
    await act(async () => finish[0](done))
    expect(pending()).toBe(true)
    await act(async () => finish[1](done))
    expect(pending()).toBe(false)
  })

  it('never says so when nothing is missing', async () => {
    const seen: boolean[] = []
    const stop = useAppStore.subscribe((state) => seen.push(state.planTranslationsPending))
    mockFetch.mockResolvedValue({ language: 'de', translations: { 'Endurance Ride': 'Grundlagenfahrt' }, missing: false })

    renderHook(() => usePlanTranslationSync())
    await waitFor(() => expect(useAppStore.getState().planTranslations['Endurance Ride']).toBe('Grundlagenfahrt'))

    expect(seen).not.toContain(true)
    expect(mockWrite).not.toHaveBeenCalled()
    stop()
  })

  it('names no text in an English interface, and no empty one', () => {
    useAppStore.setState({ planTranslationsPending: true })
    const { result, rerender } = renderHook(() => usePlanTextPending())

    expect(result.current('Steady Z2.')).toBe(true)
    expect(result.current(' ')).toBe(false)
    expect(result.current(undefined)).toBe(false)

    act(() => useAppStore.setState({ uiLanguage: 'en' }))
    rerender()
    expect(result.current('Steady Z2.')).toBe(false)
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
