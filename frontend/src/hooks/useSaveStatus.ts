import { useCallback, useEffect, useRef, useState } from 'react'

export type SaveState = 'idle' | 'saving' | 'saved' | { error: string }

/** How long "Saved" stays next to a field before it goes quiet again. */
const SAVED_VISIBLE_MS = 2000

/** The state of one field's save, for settings that save themselves
 *  (ai-trainer-ops#93). `run` saves and reports; `fail` reports a value that
 *  was never sent. */
export function useSaveStatus() {
  const [state, setState] = useState<SaveState>('idle')
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const run = useCallback(async (save: () => Promise<unknown>): Promise<boolean> => {
    window.clearTimeout(timer.current)
    setState('saving')
    try {
      await save()
      setState('saved')
      timer.current = window.setTimeout(() => setState('idle'), SAVED_VISIBLE_MS)
      return true
    } catch (e) {
      setState({ error: e instanceof Error && e.message ? e.message : 'Could not save. Please try again.' })
      return false
    }
  }, [])

  const fail = useCallback((error: string) => {
    window.clearTimeout(timer.current)
    setState({ error })
  }, [])

  // Editing the field again answers an error; it stays only until then.
  const clearError = useCallback(() => {
    setState((current) => (typeof current === 'object' ? 'idle' : current))
  }, [])

  return { state, run, fail, clearError }
}
