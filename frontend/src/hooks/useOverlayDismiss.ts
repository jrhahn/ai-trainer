import { useEffect } from 'react'
import type { RefObject } from 'react'

/** The behaviour every overlay on this site owes the keyboard.
 *
 * Locks body scroll, moves focus into the panel, keeps Tab inside it, closes on
 * Escape, and hands focus back to whatever opened it.  Written once for the
 * mobile drawer in Layout; the calendar overlay needs exactly the same thing,
 * and a second copy would be a second place for it to drift.
 *
 * Does nothing while ``open`` is false, so it is safe to call unconditionally.
 */
export function useOverlayDismiss(
  open: boolean,
  onClose: () => void,
  panelRef: RefObject<HTMLElement | null>
): void {
  useEffect(() => {
    if (!open) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const focusables = () =>
      Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      )
    focusables()[0]?.focus()

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key === 'Tab') {
        const items = focusables()
        if (items.length === 0) return
        const first = items[0]
        const last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
      previouslyFocused?.focus?.()
    }
    // onClose is called, never compared: re-running on every render of the
    // caller would tear the listener down and steal focus back mid-interaction.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
}
