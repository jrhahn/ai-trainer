import { useEffect, useRef } from 'react'

/** The depth stored on the history entry an active back gesture pushed. */
const depthOf = (state: unknown): number => {
  const depth = (state as { overlay?: unknown } | null)?.overlay
  return typeof depth === 'number' ? depth : 0
}

/** A release whose history.back() has not run yet.  StrictMode unmounts and
 *  remounts an effect at once, and a re-armed gesture does the same; the
 *  remount takes the entry over instead of stacking a second one that the late
 *  back() would then pop. */
let pendingBack: { timer: ReturnType<typeof setTimeout>; depth: number } | null = null

/** Lets the phone's back gesture call ``onBack`` instead of leaving the page.
 *
 * While ``active``, a history entry at the same address sits on top; back pops
 * it and calls ``onBack``.  Going inactive any other way takes the entry off
 * again.  A change of ``rearm`` re-arms it, for a step that goes back one at a
 * time.  Without it, back left the page and dropped a half-filled form with it
 * (review of 2026-11-01, ai-trainer-ops#338 A12).
 *
 * A link that navigates while it is active should navigate with ``replace``,
 * so the entry does not stay behind under the new page.
 */
export function useBackGesture(active: boolean, onBack: () => void, rearm?: unknown): void {
  const onBackRef = useRef(onBack)
  useEffect(() => {
    onBackRef.current = onBack
  })

  useEffect(() => {
    if (!active) return
    let depth: number
    if (pendingBack && depthOf(window.history.state) === pendingBack.depth) {
      depth = pendingBack.depth
    } else {
      depth = depthOf(window.history.state) + 1
      // Keeps the router's own fields, so it reads this as the page it is on.
      window.history.pushState({ ...(window.history.state ?? {}), overlay: depth }, '')
    }
    if (pendingBack) clearTimeout(pendingBack.timer)
    pendingBack = null

    const onPopState = () => {
      // A gesture armed on top of this one owns the entry back just popped.
      if (depthOf(window.history.state) >= depth) return
      onBackRef.current()
    }
    window.addEventListener('popstate', onPopState)
    return () => {
      window.removeEventListener('popstate', onPopState)
      // Released some other way while the entry is still on top: take it off.
      // Not when a link or a save navigated away, or back would undo that.
      const timer = setTimeout(() => {
        pendingBack = null
        if (depthOf(window.history.state) === depth) window.history.back()
      }, 0)
      pendingBack = { timer, depth }
    }
  }, [active, rearm])
}
