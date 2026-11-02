import { useEffect, useState } from 'react'

/** Below Tailwind's `sm` (640 px). */
const PHONE_QUERY = '(max-width: 639px)'

function matches(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(PHONE_QUERY).matches
    : false
}

/**
 * Whether the screen is phone-narrow, for a layout that is a different
 * structure there rather than the same one smaller: the calendar's month grid
 * becomes a list (review of 2026-11-01, ai-trainer-ops#338 A7). Without
 * `matchMedia` (jsdom) it is the wide layout.
 */
export function useIsPhone(): boolean {
  const [phone, setPhone] = useState(matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(PHONE_QUERY)
    const update = () => setPhone(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return phone
}
