import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { setProfile, useProfile } from '../queries/profile'
import { updateCurrentUser } from '../services/user'
import { useAuthToken } from './useAuthToken'
import type { useSaveStatus } from './useSaveStatus'

/** What the profile fields in Settings share (ai-trainer-ops#274: out of `SettingsPage`). */
export function useProfileSave() {
  const authToken = useAuthToken()
  const userProfile = useProfile()
  const { setFtpPlausibilityWarning } = useAppStore(
    useShallow((s) => ({
      setFtpPlausibilityWarning: s.setFtpPlausibilityWarning,
    })),
  )

  /** Settings save themselves (ai-trainer-ops#93): a field when the athlete
   *  leaves it, a choice when it is made. This section alone had five save
   *  buttons, and a value typed into one card stayed unsaved when the athlete
   *  pressed the button of another. */
  const saveFields = async (
    status: ReturnType<typeof useSaveStatus>,
    updates: Parameters<typeof updateCurrentUser>[1],
    then: () => void,
  ): Promise<UserProfile | undefined> => {
    if (!authToken || !userProfile) return undefined
    let saved: UserProfile | undefined
    await status.run(async () => {
      const updated = await updateCurrentUser(authToken, updates)
      setProfile(updated.profile)
      setFtpPlausibilityWarning(updated.ftpPlausibilityWarning)
      saved = updated.profile
      then()
    })
    return saved
  }

  return { saveFields }
}

// The draft goes once its value is stored, unless the athlete has typed
// something else meanwhile: that is a new edit, and its own blur saves it.
export const dropDraftIfSent =
  (set: (update: (current: string | null) => string | null) => void, sent: string) => () =>
    set((current) => (current === sent ? null : current))

// A field saves when it is left; Enter leaves it.
export const leaveOnEnter = (e: KeyboardEvent<HTMLInputElement>) => {
  if (e.key === 'Enter') e.currentTarget.blur()
}

export type Note = { type: 'success' | 'error'; text: string } | null

/** A line under a setting that says what happened. With `hideAfterMs` it goes
 *  again, and its timer goes with the card (ai-trainer-ops#274: four of these
 *  timers were never cleared). */
export function useNote() {
  const [note, setNote] = useState<Note>(null)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const show = (next: Note, hideAfterMs?: number) => {
    window.clearTimeout(timer.current)
    setNote(next)
    if (next && hideAfterMs) timer.current = window.setTimeout(() => setNote(null), hideAfterMs)
  }
  return [note, show] as const
}
