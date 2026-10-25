import { useEffect, useRef } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { useProfile } from '../queries/profile'
import type { RideMetricPoint } from '../store/useAppStore'
import { useT } from '../i18n'
import { processPendingFeedbacks, refreshLoginSummary, refreshTrainingStatus } from '../services/ai'
import { accountKey, readLocal, writeLocal } from '../lib/storage'
import { rideActivityKey, rideActivityRefreshId } from '../utils/rideDedupe'
import { LOGIN_SUMMARY_QUERY_KEY, TRAINING_STATUS_QUERY_KEY } from '../queries/keys'

// Per account (ai-trainer-ops#269), like the last login on the dashboard.
const SUMMARY_REFRESH_KEY = 'ai_trainer_summary_refresh_activity_ids'
const SUMMARY_REFRESH_VERSION = 'latest-activity-v6'
// A summary or badge the model just wrote is good for this long: another
// dashboard within it uses the answer instead of asking again, which spent
// the AI allowance on every hop to the day page and back (ai-trainer-ops#277).
const GENERATED_STALE_MS = 5 * 60 * 1000

/** The coach's summary of the recent training and the status badge: written
 *  when missing, and again after a new ride. Returns whether a summary is
 *  being prepared. Out of `DashboardPage` (ai-trainer-ops#274); the two that are
 *  written when missing are queries (ai-trainer-ops#277). */
export function useTrainingSummary(latestRecentRide: RideMetricPoint | null): boolean {
  const t = useT()
  const account = useProfile()?.email ?? ''
  const { authToken, riderAssessment, setRiderAssessment, showWarning } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      showWarning: s.showWarning,
    }))
  )
  const summaryRefreshKeyRef = useRef<string | null>(null)
  const latestRideActivityKey = latestRecentRide ? rideActivityKey(latestRecentRide) : ''
  const latestRideActivityId = latestRecentRide ? rideActivityRefreshId(latestRecentRide) : null
  const latestRideSummaryKey = latestRideActivityKey
    ? `${SUMMARY_REFRESH_VERSION}:${latestRideActivityKey}`
    : ''

  // Auto-generate loginSummary if the user has a riderAssessment but no summary yet,
  // or if the stored summary looks truncated (no bullet points / too short).
  const summaryIncomplete = (s: string | null | undefined) =>
    !s || s.length < 60 || !s.includes('- ')

  // Once per mount before, the model's answer kept for a while now. Not on
  // focus or reconnect either: each fetch is a model call.
  const generated = {
    staleTime: GENERATED_STALE_MS,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  } as const

  // After a new ride: once per ride, remembered per account in storage.
  const { mutate: takeInLatestRide, isPending: takingInRide, isIdle: rideNotTakenIn } = useMutation({
    mutationFn: ({ ids }: { ids: Array<number | string>; key: string }) => processPendingFeedbacks(authToken!, ids),
    onSuccess: (text, { key }) => {
      const current = useAppStore.getState().riderAssessment
      if (text && current) {
        setRiderAssessment({ ...current, loginSummary: text })
        writeLocal(accountKey(SUMMARY_REFRESH_KEY, account), key)
      }
    },
    // The summary before the ride stays on the card.
    onError: () =>
      showWarning(t('Your training summary could not be updated with your latest ride. It tries again on your next visit.')),
  })

  // Due: a ride the summary has not taken in yet. Its refresh writes a new
  // summary, so a missing one waits for it and is asked for only if it brings
  // none. Since #245 every ride clears the summary, and the dashboard asked the
  // model twice for the same ride (ai-trainer-ops#277).
  const rideRefreshDue =
    !!authToken &&
    !!riderAssessment &&
    !!latestRecentRide &&
    latestRideActivityId != null &&
    !!latestRideSummaryKey &&
    !!account &&
    readLocal(accountKey(SUMMARY_REFRESH_KEY, account)) !== latestRideSummaryKey
  const waitForRide = rideRefreshDue && (rideNotTakenIn || takingInRide)

  const summary = useQuery({
    queryKey: [LOGIN_SUMMARY_QUERY_KEY, authToken],
    queryFn: async () => {
      try {
        const text = await refreshLoginSummary(authToken!)
        const current = useAppStore.getState().riderAssessment
        if (text && current) setRiderAssessment({ ...current, loginSummary: text })
        return text
      } catch (error) {
        // Said, not swallowed: the card simply did not show (ai-trainer-ops#272).
        showWarning(t('Your training summary could not be loaded. It tries again on your next visit.'))
        throw error
      }
    },
    enabled: !!authToken && !!riderAssessment && summaryIncomplete(riderAssessment.loginSummary) && !waitForRide,
    ...generated,
  })

  // Lazily regenerate the status badge when the pipeline has invalidated it (or
  // the athlete never had one). A failure just leaves the segment out rather
  // than blocking the dashboard.
  useQuery({
    queryKey: [TRAINING_STATUS_QUERY_KEY, authToken],
    queryFn: async () => {
      try {
        const badge = await refreshTrainingStatus(authToken!)
        const current = useAppStore.getState().riderAssessment
        if (badge && current) {
          setRiderAssessment({
            ...current,
            trainingStatusLabel: badge.label,
            trainingStatusTone: badge.tone,
            trainingStatusRationale: badge.rationale,
          })
        }
        return badge
      } catch (error) {
        showWarning(t('Your training status could not be loaded. Reload the page to try again.'))
        throw error
      }
    },
    enabled: !!authToken && !!riderAssessment && !riderAssessment.trainingStatusLabel,
    ...generated,
  })

  useEffect(() => {
    // Not before the profile either: without it the key would have no account,
    // and the ref below would keep the real one from ever being written (review
    // of #1062). Unavailable storage reads as null; the in-memory guard still
    // prevents loops.
    if (!rideRefreshDue || latestRideActivityId == null) return
    if (summaryRefreshKeyRef.current === latestRideSummaryKey) return

    summaryRefreshKeyRef.current = latestRideSummaryKey
    takeInLatestRide({ ids: [latestRideActivityId], key: latestRideSummaryKey })
  }, [rideRefreshDue, latestRideActivityId, latestRideSummaryKey, takeInLatestRide])

  return summary.isFetching || takingInRide
}
