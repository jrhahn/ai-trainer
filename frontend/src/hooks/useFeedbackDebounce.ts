import { useEffect, useRef } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { processPendingFeedbacks } from '../services/ai'
import { fetchRideMetricsHistory } from '../services/user'

const DEBOUNCE_MS = 60_000

/**
 * Watches `pendingFeedbackRideIds` in the store.  Whenever rides are added,
 * starts (or resets) a 60-second timer.  When the timer fires without further
 * additions, sends all pending ride IDs to the backend in one call, updates the
 * login summary in the store, and refreshes rideMetricsHistory.
 *
 * Returns `{ isPending }` so the UI can show a "summary updating" indicator.
 */
export function useFeedbackDebounce(): { isPending: boolean } {
  const {
    authToken,
    pendingFeedbackRideIds,
    riderAssessment,
    setRiderAssessment,
    setRideMetricsHistory,
    clearPendingFeedbackRides,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      pendingFeedbackRideIds: s.pendingFeedbackRideIds,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      setRideMetricsHistory: s.setRideMetricsHistory,
      clearPendingFeedbackRides: s.clearPendingFeedbackRides,
    }))
  )

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const authTokenRef = useRef(authToken)
  const riderAssessmentRef = useRef(riderAssessment)
  authTokenRef.current = authToken
  riderAssessmentRef.current = riderAssessment

  useEffect(() => {
    if (pendingFeedbackRideIds.length === 0) {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      return
    }

    // Reset timer on every new addition
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
    }

    const ids = [...pendingFeedbackRideIds]

    timerRef.current = setTimeout(async () => {
      timerRef.current = null
      const token = authTokenRef.current
      if (!token) return

      try {
        const loginSummary = await processPendingFeedbacks(token, ids)
        if (loginSummary) {
          const current = riderAssessmentRef.current
          setRiderAssessment(
            current
              ? { ...current, loginSummary }
              : { riderType: 'allrounder', notes: '', loginSummary }
          )
        }
        // Refresh ride metrics so userNote values are up to date
        const freshMetrics = await fetchRideMetricsHistory(token)
        setRideMetricsHistory(freshMetrics)
      } catch {
        // Silently ignore — the user_note is already persisted in the DB
      } finally {
        clearPendingFeedbackRides()
      }
    }, DEBOUNCE_MS)

    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFeedbackRideIds.length])

  return { isPending: pendingFeedbackRideIds.length > 0 }
}
