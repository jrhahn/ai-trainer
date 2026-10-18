import { useEffect, useState } from 'react'
import { fetchSignupConfig } from '../services/auth'

// One answer per page load: the setting changes with a deploy, not while a
// visitor reads the page.
let cached: Promise<boolean> | null = null

/** Test seam: forget the cached answer. */
export function resetInviteOnlyCache(): void {
  cached = null
}

/**
 * Whether sign-up needs an invitation code (ops#108, #96). False until the
 * server has answered, and when it cannot be asked: the form then shows no
 * code field, and a closed server still refuses with a message that says why.
 */
export function useInviteOnly(): boolean {
  const [inviteOnly, setInviteOnly] = useState(false)
  useEffect(() => {
    let live = true
    // Through a resolved promise, so a failure thrown before the request is
    // made still lands in the catch.
    cached ??= Promise.resolve()
      .then(() => fetchSignupConfig())
      .then((config) => Boolean(config?.inviteOnly))
      .catch(() => {
        cached = null
        return false
      })
    void cached.then((value) => {
      if (live) setInviteOnly(value)
    })
    return () => {
      live = false
    }
  }, [])
  return inviteOnly
}
