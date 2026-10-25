import { useEffect, useSyncExternalStore } from 'react'
import { useAppStore } from '../store/useAppStore'
import { getStravaImportProgress, type ImportProgress } from '../services/strava'

export type { ImportProgress }

const POLL_MS = 3000

const INITIAL_PROGRESS: ImportProgress = {
  status: 'idle',
  total: 0,
  processed: 0,
  imported: 0,
  skipped: 0,
  failedActivities: [],
  error: '',
}

let sharedProgress: ImportProgress = INITIAL_PROGRESS
let sharedAuthToken: string | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollInFlight = false
// Number of mounted consumers that requested polling. The shared timer runs
// while this is > 0 and only stops when the last polling consumer unmounts, so
// one consumer unmounting can't freeze live updates for the others (#328).
let pollCount = 0
// Mounted consumers with a token. The last one to leave resets the shared state.
let consumerCount = 0
// Every mounted consumer, told when `sharedProgress` changes. React reads the
// value itself through `useSyncExternalStore` (ai-trainer-ops#286).
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function readProgress(): ImportProgress {
  return sharedProgress
}

function failureSignature(progress: ImportProgress): string {
  const failures = progress.failedActivities ?? []
  if (failures.length === 0) return 'none'
  const first = failures[0]
  const last = failures[failures.length - 1]
  return `${failures.length}:${first.activityId ?? 'x'}:${last.activityId ?? 'x'}:${last.reason ?? ''}`
}

function hasSameProgress(a: ImportProgress, b: ImportProgress): boolean {
  return (
    a.status === b.status &&
    a.total === b.total &&
    a.processed === b.processed &&
    a.imported === b.imported &&
    a.skipped === b.skipped &&
    a.error === b.error &&
    failureSignature(a) === failureSignature(b)
  )
}

function normalizeProgress(progress: ImportProgress): ImportProgress {
  // Keep running payloads light in the UI; detailed failures are shown once done/error.
  if (progress.status === 'running' && progress.failedActivities.length > 0) {
    return { ...progress, failedActivities: [] }
  }
  return progress
}

function publish(progress: ImportProgress) {
  sharedProgress = progress
  listeners.forEach((listener) => listener())
}

async function pollOnce() {
  if (!sharedAuthToken || pollInFlight) return
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return

  pollInFlight = true
  try {
    const data = normalizeProgress(await getStravaImportProgress(sharedAuthToken))
    if (!hasSameProgress(sharedProgress, data)) {
      publish(data)
    }
  } catch {
    // silently ignore network errors during polling
  } finally {
    pollInFlight = false
  }
}

function startPolling() {
  if (pollTimer !== null) return
  void pollOnce()
  pollTimer = setInterval(() => void pollOnce(), POLL_MS)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  pollInFlight = false
}

interface UseImportProgressOptions {
  poll?: boolean
}

export function useImportProgress(options: UseImportProgressOptions = {}) {
  const poll = options.poll ?? false
  const authToken = useAppStore((s) => s.authToken)
  const progress = useSyncExternalStore(subscribe, readProgress)

  useEffect(() => {
    if (!authToken) {
      if (consumerCount === 0) {
        sharedProgress = INITIAL_PROGRESS
      }
      return
    }

    sharedAuthToken = authToken
    consumerCount += 1

    return () => {
      consumerCount -= 1
      if (consumerCount === 0) {
        stopPolling()
        pollCount = 0
        // Reset shared cache so the next consumer starts from a clean state
        // rather than seeing stale progress from a previous session.
        sharedProgress = INITIAL_PROGRESS
        sharedAuthToken = null
      }
    }
  }, [authToken])

  // Polling apart from the consumer count, and after it, so the token is set
  // before the first poll. In one effect, a page that stopped polling once its
  // import was done ran the cleanup above: as the only consumer it reset the
  // result it was about to show (ai-trainer-ops#286).
  useEffect(() => {
    if (!authToken || !poll) return
    pollCount += 1
    startPolling()
    return () => {
      pollCount = Math.max(0, pollCount - 1)
      // Consumers may remain, but none want live updates any more.
      if (pollCount === 0) stopPolling()
    }
  }, [authToken, poll])

  // Signed out: nothing to show, whatever the shared value still holds.
  return authToken ? progress : INITIAL_PROGRESS
}
