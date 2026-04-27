import { useEffect, useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { getStravaImportProgress, type ImportProgress } from '../services/strava'

export type { ImportProgress }

const POLL_MS = 1500

export function useImportProgress() {
  const authToken = useAppStore((s) => s.authToken)
  const [progress, setProgress] = useState<ImportProgress>({
    status: 'idle',
    total: 0,
    processed: 0,
    imported: 0,
    skipped: 0,
    failedActivities: [],
    error: '',
  })

  useEffect(() => {
    if (!authToken) return
    let cancelled = false

    const poll = async () => {
      if (cancelled) return
      try {
        const data = await getStravaImportProgress(authToken)
        if (!cancelled) setProgress(data)
      } catch {
        // silently ignore network errors during polling
      }
    }

    void poll()
    const id = setInterval(() => void poll(), POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [authToken])

  return progress
}
