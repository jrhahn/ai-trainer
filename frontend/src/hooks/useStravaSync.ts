import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type StravaActivity } from '../store/useAppStore'
import { getStravaActivities, getNewStravaActivities } from '../services/strava'
import { getIntervalsActivities, getNewIntervalsActivities } from '../services/intervals'
import { analyseStravaActivities, generateTrainingPlan } from '../services/ai'
import { saveTrainingPlan, updateCurrentUser } from '../services/user'
import { useMetricsPipeline } from './useMetricsPipeline'

const POLL_INTERVAL_MS = 5 * 60 * 1000 // 5 minutes

export type AnalysisStatus = 'idle' | 'analysing' | 'done' | 'error'

export interface UseStravaSyncResult {
  stravaActivities: StravaActivity[]
  analysisStatus: AnalysisStatus
  analysisError: string
  newActivitiesCount: number
}

export function useStravaSync(): UseStravaSyncResult {
  const {
    authToken,
    userProfile,
    trainingPlan,
    stravaConnection,
    intervalsConnection,
    stravaAnalysisComplete,
    lastStravaActivityId,
    stravaAutoSyncEnabled,
    intervalsAnalysisComplete,
    lastIntervalsActivityId,
    intervalsAutoSyncEnabled,
    setRiderAssessment,
    setStravaAnalysisComplete,
    setLastStravaActivityId,
    setIntervalsAnalysisComplete,
    setLastIntervalsActivityId,
    setTrainingPlan,
    setUserProfile,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      stravaConnection: s.stravaConnection,
      intervalsConnection: s.intervalsConnection,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
      lastStravaActivityId: s.lastStravaActivityId,
      stravaAutoSyncEnabled: s.stravaAutoSyncEnabled,
      intervalsAnalysisComplete: s.intervalsAnalysisComplete,
      lastIntervalsActivityId: s.lastIntervalsActivityId,
      intervalsAutoSyncEnabled: s.intervalsAutoSyncEnabled,
      setRiderAssessment: s.setRiderAssessment,
      setStravaAnalysisComplete: s.setStravaAnalysisComplete,
      setLastStravaActivityId: s.setLastStravaActivityId,
      setIntervalsAnalysisComplete: s.setIntervalsAnalysisComplete,
      setLastIntervalsActivityId: s.setLastIntervalsActivityId,
      setTrainingPlan: s.setTrainingPlan,
      setUserProfile: s.setUserProfile,
    }))
  )

  const queryClient = useQueryClient()
  const { recalculateAll } = useMetricsPipeline()
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle')
  const [analysisError, setAnalysisError] = useState('')
  const [newActivitiesCount, setNewActivitiesCount] = useState(0)
  // Guard: prevents double-triggering when React batches setState calls from
  // runAnalysis (e.g. setUserProfile) before stravaAnalysisComplete flips.
  const isAnalysingRef = useRef(false)
  const activeSource = stravaConnection && stravaAutoSyncEnabled
    ? 'strava'
    : intervalsConnection && intervalsAutoSyncEnabled
      ? 'intervals'
      : null
  const activeAnalysisComplete = activeSource === 'intervals' ? intervalsAnalysisComplete : stravaAnalysisComplete
  const activeLastActivityId = activeSource === 'intervals' ? lastIntervalsActivityId : lastStravaActivityId

  const runAnalysis = async (activities: StravaActivity[], isIncremental = false, source = activeSource) => {
    if (!authToken || !userProfile || activities.length === 0) return
    if (!source) return
    isAnalysingRef.current = true
    setAnalysisStatus('analysing')
    setAnalysisError('')
    try {
      const { assessment, planUpdates } = await analyseStravaActivities(activities, authToken, userProfile.maxHeartRate)
      setRiderAssessment(assessment)

      const updatedProfile = {
        ...userProfile,
        currentFTP: userProfile.currentFTP,
        maxHeartRate: userProfile.maxHeartRate,
      }
      setUserProfile(updatedProfile)

      const newestId = Math.max(...activities.map((a) => a.id))
      if (source === 'intervals') {
        setLastIntervalsActivityId(newestId)
      } else {
        setLastStravaActivityId(newestId)
      }

      await updateCurrentUser(authToken, {
        currentFTP: updatedProfile.currentFTP,
        maxHeartRate: updatedProfile.maxHeartRate,
        ...(source === 'intervals'
          ? { intervalsAnalysisComplete: true, lastIntervalsActivityId: newestId }
          : { stravaAnalysisComplete: true, lastStravaActivityId: newestId }),
      })

      // Recompute all historical TSS/CTL/ATL/TSB with the (potentially updated) FTP.
      await recalculateAll(updatedProfile.currentFTP).catch(() => { /* best-effort */ })

      if (isIncremental && planUpdates && planUpdates.length > 0) {
        // For new activities, apply targeted plan updates rather than regenerating the whole plan
        const updatesByDate = Object.fromEntries(planUpdates.map((u) => [u.date, u]))
        const updatedPlan = trainingPlan.map((day) =>
          updatesByDate[day.date] ? { ...day, ...updatesByDate[day.date] } : day
        )
        setTrainingPlan(updatedPlan)
        // Persist the updated plan
        await saveTrainingPlan(authToken, updatedPlan)
      } else {
        // First-time analysis or no targeted updates → regenerate the full plan
        const updatedPlan = await generateTrainingPlan(authToken)
        await saveTrainingPlan(authToken, updatedPlan)
        setTrainingPlan(updatedPlan)
      }

      if (source === 'intervals') {
        setIntervalsAnalysisComplete(true)
      } else {
        setStravaAnalysisComplete(true)
      }
      setAnalysisStatus('done')
    } catch (e) {
      setAnalysisError(e instanceof Error ? e.message : 'Analysis failed')
      setAnalysisStatus('error')
    } finally {
      isAnalysingRef.current = false
    }
  }

  // Fetch all Strava activities. TanStack Query handles caching so the list
  // is not re-requested on every re-render or page navigation.
  const activitiesQueryEnabled = !!activeSource && !!authToken && !!userProfile
  const {
    data: stravaActivities = [],
    isError: isActivitiesError,
  } = useQuery({
    queryKey: [activeSource === 'intervals' ? 'intervalsActivities' : 'stravaActivities', authToken],
    queryFn: () => activeSource === 'intervals' ? getIntervalsActivities(authToken!) : getStravaActivities(authToken!),
    enabled: activitiesQueryEnabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })

  // Trigger initial analysis when activities are first loaded
  useEffect(() => {
    if (!activeSource || !authToken || !userProfile) return
    if (stravaActivities.length > 0 && !activeAnalysisComplete && !isAnalysingRef.current) {
      void runAnalysis(stravaActivities, false, activeSource)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authToken, activeSource, activeAnalysisComplete, userProfile?.email, stravaActivities])

  // Propagate fetch errors to the analysis status
  useEffect(() => {
    if (isActivitiesError) {
      setAnalysisStatus('error')
      setAnalysisError(`Failed to fetch ${activeSource === 'intervals' ? 'Intervals.icu' : 'Strava'} activities`)
    }
  }, [activeSource, isActivitiesError])

  // Poll for new Strava activities every 5 minutes. TanStack Query's
  // refetchInterval replaces the manual setTimeout polling loop.
  const { data: polledNewActivities } = useQuery({
    queryKey: [activeSource === 'intervals' ? 'newIntervalsActivities' : 'newStravaActivities', authToken, activeLastActivityId],
    queryFn: () =>
      activeSource === 'intervals'
        ? getNewIntervalsActivities(authToken!, activeLastActivityId!)
        : getNewStravaActivities(authToken!, activeLastActivityId!),
    enabled: !!activeSource && !!authToken && activeAnalysisComplete && activeLastActivityId !== null,
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  })

  // Process new activities returned by the poll query. Structural sharing in
  // TanStack Query ensures this effect only re-runs when the data actually changes.
  const processedNewActivitiesRef = useRef<Set<number>>(new Set())
  useEffect(() => {
    if (!polledNewActivities || polledNewActivities.length === 0) return
    const unprocessed = polledNewActivities.filter(
      (a) => !processedNewActivitiesRef.current.has(a.id)
    )
    if (unprocessed.length === 0) return
    unprocessed.forEach((a) => processedNewActivitiesRef.current.add(a.id))

    setNewActivitiesCount(unprocessed.length)
    // Merge new activities into the main query cache
    const activitiesKey = activeSource === 'intervals' ? 'intervalsActivities' : 'stravaActivities'
    queryClient.setQueryData<StravaActivity[]>([activitiesKey, authToken], (prev = []) => {
      const existingIds = new Set(prev.map((a) => a.id))
      return [...unprocessed.filter((a) => !existingIds.has(a.id)), ...prev]
    })
    void runAnalysis(unprocessed, true, activeSource).then(() => setNewActivitiesCount(0))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polledNewActivities])

  return { stravaActivities, analysisStatus, analysisError, newActivitiesCount }
}
