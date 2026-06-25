import { useCallback, useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'
import {
  fetchCurrentUser,
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  recalculateMetrics,
  updateCurrentUser,
} from '../services/user'

export interface RecalcResult {
  updated: number
  ftpUsed: number
}

export interface UseMetricsPipelineResult {
  /**
   * Persists the given profile updates to the backend, applies them
   * optimistically to the store, then runs a full historical recalculation
   * and refreshes both metricsHistory and rideMetricsHistory in the store.
   *
   * Use this whenever a fitness metric (FTP, maxHR, restingHR, thresholdHR)
   * changes so that all historical ride TSS/CTL/ATL/TSB values stay
   * consistent with the new values.
   */
  updateMetrics: (updates: Partial<UserProfile>) => Promise<RecalcResult>

  /**
   * Rebuilds all historical TSS/CTL/ATL/TSB values using the current (or
   * provided) FTP, then refreshes metricsHistory and rideMetricsHistory.
   *
   * Does NOT update the user profile. Use this after a Strava import
   * completes (new rides added, metrics unchanged) or when you want to
   * recalculate without changing any profile fields.
   */
  recalculateAll: (ftpOverride?: number) => Promise<RecalcResult>

  /** True while any pipeline operation is in progress. */
  isPending: boolean

  /** Error message from the last failed pipeline call, or null. */
  error: string | null
}

export function useMetricsPipeline(): UseMetricsPipelineResult {
  const { authToken, userProfile, setUserProfile, setRiderAssessment, setMetricsHistory, setRideMetricsHistory } =
    useAppStore(
      useShallow((s) => ({
        authToken: s.authToken,
        userProfile: s.userProfile,
        setUserProfile: s.setUserProfile,
        setRiderAssessment: s.setRiderAssessment,
        setMetricsHistory: s.setMetricsHistory,
        setRideMetricsHistory: s.setRideMetricsHistory,
      }))
    )

  const [isPending, setIsPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Internal: runs the recalculation and refreshes store slices.
  // Does not manage isPending — callers own that.
  const _recalcCore = useCallback(
    async (ftpOverride?: number): Promise<RecalcResult> => {
      const token = authToken
      if (!token) throw new Error('Not authenticated')

      const result = await recalculateMetrics(token, ftpOverride)
      const [currentUser, metrics, rideMetrics] = await Promise.all([
        fetchCurrentUser(token),
        fetchMetricsHistory(token),
        fetchRideMetricsHistory(token),
      ])
      setUserProfile(currentUser.profile)
      setRiderAssessment(currentUser.riderAssessment)
      setMetricsHistory(metrics)
      setRideMetricsHistory(rideMetrics)
      return result
    },
    [authToken, setMetricsHistory, setRideMetricsHistory, setRiderAssessment, setUserProfile]
  )

  const recalculateAll = useCallback(
    async (ftpOverride?: number): Promise<RecalcResult> => {
      if (!authToken) throw new Error('Not authenticated')
      setIsPending(true)
      setError(null)
      try {
        return await _recalcCore(ftpOverride)
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Recalculation failed'
        setError(msg)
        throw e
      } finally {
        setIsPending(false)
      }
    },
    [authToken, _recalcCore]
  )

  const updateMetrics = useCallback(
    async (updates: Partial<UserProfile>): Promise<RecalcResult> => {
      if (!authToken || !userProfile) throw new Error('Not authenticated')
      const profileSnapshot = userProfile
      setIsPending(true)
      setError(null)
      try {
        await updateCurrentUser(authToken, updates)
        setUserProfile({ ...userProfile, ...updates })
        return await _recalcCore(updates.currentFTP)
      } catch (e) {
        setUserProfile(profileSnapshot)
        const msg = e instanceof Error ? e.message : 'Failed to update metrics'
        setError(msg)
        throw e
      } finally {
        setIsPending(false)
      }
    },
    [authToken, userProfile, setUserProfile, _recalcCore]
  )

  return { updateMetrics, recalculateAll, isPending, error }
}
