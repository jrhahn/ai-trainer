import { useEffect, useState } from 'react'
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
const listeners = new Set<(progress: ImportProgress) => void>()

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
  listeners.forEach((listener) => listener(progress))
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

export function useImportProgress() {
  const authToken = useAppStore((s) => s.authToken)
  const [progress, setProgress] = useState<ImportProgress>(sharedProgress)

  useEffect(() => {
    if (!authToken) {
      setProgress(INITIAL_PROGRESS)
      if (listeners.size === 0) {
        sharedProgress = INITIAL_PROGRESS
      }
      return
    }

    sharedAuthToken = authToken
    listeners.add(setProgress)
    setProgress(sharedProgress)
    startPolling()

    return () => {
      listeners.delete(setProgress)
      if (listeners.size === 0) {
        stopPolling()
      }
    }
  }, [authToken])

  return progress
}
