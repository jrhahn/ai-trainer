import { useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { useT } from '../i18n'
import { processPendingFeedbacks, refreshLoginSummary, refreshTrainingStatus } from '../services/ai'
import { accountKey, readLocal, writeLocal } from '../lib/storage'
import { rideActivityKey, rideActivityRefreshId } from '../utils/rideDedupe'

// Per account (ai-trainer-ops#269), like the last login on the dashboard.
const SUMMARY_REFRESH_KEY = 'ai_trainer_summary_refresh_activity_ids'
const SUMMARY_REFRESH_VERSION = 'latest-activity-v6'

/** The coach's summary of the recent training and the status badge: written
 *  once when missing, and again after a new ride. Returns whether a summary is
 *  being prepared. Out of `DashboardPage` (ai-trainer-ops#274). */
export function useTrainingSummary(latestRecentRide: RideMetricPoint | null): boolean {
  const t = useT()
  const { account, authToken, riderAssessment, setRiderAssessment, showWarning } = useAppStore(
    useShallow((s) => ({
      account: s.userProfile?.email ?? '',
      authToken: s.authToken,
      riderAssessment: s.riderAssessment,
      setRiderAssessment: s.setRiderAssessment,
      showWarning: s.showWarning,
    }))
  )
  const summaryTriggeredRef = useRef(false)
  const statusTriggeredRef = useRef(false)
  const summaryRefreshKeyRef = useRef<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const latestRideActivityKey = latestRecentRide ? rideActivityKey(latestRecentRide) : ''
  const latestRideActivityId = latestRecentRide ? rideActivityRefreshId(latestRecentRide) : null
  const latestRideSummaryKey = latestRideActivityKey
    ? `${SUMMARY_REFRESH_VERSION}:${latestRideActivityKey}`
    : ''

  // Auto-generate loginSummary once if the user has a riderAssessment but no summary yet,
  // or if the stored summary looks truncated (no bullet points / too short).
  const summaryIncomplete = (s: string | null | undefined) =>
    !s || s.length < 60 || !s.includes('- ')

  useEffect(() => {
    if (!authToken || !riderAssessment || !summaryIncomplete(riderAssessment.loginSummary) || summaryTriggeredRef.current) return
    summaryTriggeredRef.current = true
    setSummaryLoading(true)
    refreshLoginSummary(authToken)
      .then((summary) => {
        if (summary) {
          setRiderAssessment({ ...riderAssessment, loginSummary: summary })
        }
      })
      // Said, not swallowed: the card simply did not show (ai-trainer-ops#272).
      .catch(() => showWarning(t('Your training summary could not be loaded. It tries again on your next visit.')))
      .finally(() => setSummaryLoading(false))
  }, [authToken, riderAssessment, setRiderAssessment, showWarning])

  // Lazily regenerate the status badge when the pipeline has invalidated it (or
  // the athlete never had one). Cheap to skip, so it is fire-and-forget: a
  // failure just leaves the segment out rather than blocking the dashboard.
  useEffect(() => {
    if (!authToken || !riderAssessment || riderAssessment.trainingStatusLabel) return
    if (statusTriggeredRef.current) return
    statusTriggeredRef.current = true
    refreshTrainingStatus(authToken)
      .then((badge) => {
        if (!badge) return
        setRiderAssessment({
          ...riderAssessment,
          trainingStatusLabel: badge.label,
          trainingStatusTone: badge.tone,
          trainingStatusRationale: badge.rationale,
        })
      })
      .catch(() => showWarning(t('Your training status could not be loaded. Reload the page to try again.')))
  }, [authToken, riderAssessment, setRiderAssessment, showWarning])

  useEffect(() => {
    if (!authToken || !riderAssessment || !latestRecentRide || latestRideActivityId == null || !latestRideSummaryKey) return
    // Not before the profile: without it the key would have no account, and the
    // ref below would keep the real one from ever being written (review of #1062).
    if (!account) return
    if (summaryRefreshKeyRef.current === latestRideSummaryKey) return

    // Unavailable storage reads as null; the in-memory guard still prevents loops.
    if (readLocal(accountKey(SUMMARY_REFRESH_KEY, account)) === latestRideSummaryKey) return

    summaryRefreshKeyRef.current = latestRideSummaryKey
    const activityIds = [latestRideActivityId]

    setSummaryLoading(true)
    processPendingFeedbacks(authToken, activityIds)
      .then((summary) => {
        if (summary) {
          setRiderAssessment({ ...riderAssessment, loginSummary: summary })
          writeLocal(accountKey(SUMMARY_REFRESH_KEY, account), latestRideSummaryKey)
        }
      })
      // The summary before the ride stays on the card.
      .catch(() =>
        showWarning(t('Your training summary could not be updated with your latest ride. It tries again on your next visit.')),
      )
      .finally(() => setSummaryLoading(false))
  }, [account, authToken, latestRecentRide, latestRideActivityId, latestRideSummaryKey, riderAssessment, setRiderAssessment, showWarning])

  return summaryLoading
}
