import { useEffect, useRef } from 'react'

/** The depth stored on the history entry an active back gesture pushed. */
const depthOf = (state: unknown): number => {
  const depth = (state as { overlay?: unknown } | null)?.overlay
  return typeof depth === 'number' ? depth : 0
}

/** The depths of the gestures armed right now. */
const armed = new Set<number>()

/** The one pending clean-up after a release.  Several releases in one tick
 *  (Escape closes a dialog and the calendar under it) share it, and StrictMode's
 *  remount, or a re-armed gesture, takes the entry over by cancelling it. */
let releaseTimer: ReturnType<typeof setTimeout> | null = null

const highestArmed = () => Math.max(0, ...armed)

/** Pops the entries above the highest armed gesture, which nothing owns any
 *  more.  Not after a link or a save navigated away: the router's entry is on
 *  top then, and back would undo the navigation. */
const releaseOrphans = () => {
  releaseTimer = null
  const top = depthOf(window.history.state)
  const highest = highestArmed()
  // Never go(0): that reloads the page.
  if (top > highest) window.history.go(highest - top)
}

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
    const top = depthOf(window.history.state)
    if (releaseTimer !== null && top > highestArmed()) {
      clearTimeout(releaseTimer)
      releaseTimer = null
      depth = top
    } else {
      depth = top + 1
      // Keeps the router's own fields, so it reads this as the page it is on.
      window.history.pushState({ ...(window.history.state ?? {}), overlay: depth }, '')
    }
    armed.add(depth)

    const onPopState = () => {
      // A gesture armed on top of this one owns the entry back just popped.
      if (depthOf(window.history.state) >= depth) return
      onBackRef.current()
    }
    window.addEventListener('popstate', onPopState)
    return () => {
      window.removeEventListener('popstate', onPopState)
      armed.delete(depth)
      // Released some other way while its entry is still there: take it off,
      // with any entry above it whose gesture went in the same tick.  A
      // gesture still armed above keeps it until that one goes too.
      if (releaseTimer !== null) clearTimeout(releaseTimer)
      releaseTimer = setTimeout(releaseOrphans, 0)
    }
  }, [active, rearm])
}
