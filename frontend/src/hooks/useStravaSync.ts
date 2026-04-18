import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type StravaActivity } from '../store/useAppStore'
import { getStravaActivities, getNewStravaActivities } from '../services/strava'
import { analyseStravaActivities, generateTrainingPlan } from '../services/ai'
import { saveTrainingPlan, updateCurrentUser } from '../services/user'

const POLL_INTERVAL_MS = 5 * 60 * 1000 // 5 minutes

// Threshold HR is typically ~87% of max HR (used to estimate max HR from threshold HR)
const THRESHOLD_HR_TO_MAX_HR_RATIO = 0.87

export type AnalysisStatus = 'idle' | 'analysing' | 'done' | 'error'

export interface UseStravaSyncResult {
  stravaActivities: StravaActivity[]
  analysisStatus: AnalysisStatus
  analysisError: string
  newRidesCount: number
}

export function useStravaSync(): UseStravaSyncResult {
  const {
    authToken,
    userProfile,
    trainingPlan,
    stravaConnection,
    stravaAnalysisComplete,
    lastStravaActivityId,
    setRiderAssessment,
    setStravaAnalysisComplete,
    setLastStravaActivityId,
    setTrainingPlan,
    setUserProfile,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      stravaConnection: s.stravaConnection,
      stravaAnalysisComplete: s.stravaAnalysisComplete,
      lastStravaActivityId: s.lastStravaActivityId,
      setRiderAssessment: s.setRiderAssessment,
      setStravaAnalysisComplete: s.setStravaAnalysisComplete,
      setLastStravaActivityId: s.setLastStravaActivityId,
      setTrainingPlan: s.setTrainingPlan,
      setUserProfile: s.setUserProfile,
    }))
  )

  const queryClient = useQueryClient()
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle')
  const [analysisError, setAnalysisError] = useState('')
  const [newRidesCount, setNewRidesCount] = useState(0)

  const runAnalysis = async (activities: StravaActivity[], isIncremental = false) => {
    if (!authToken || !userProfile || activities.length === 0) return
    setAnalysisStatus('analysing')
    setAnalysisError('')
    try {
      const { assessment, planUpdates } = await analyseStravaActivities(activities, authToken, userProfile.maxHeartRate)
      setRiderAssessment(assessment)

      const updatedProfile = {
        ...userProfile,
        currentFTP: userProfile.currentFTP ?? assessment.estimatedFTP,
        maxHeartRate: userProfile.maxHeartRate ?? (assessment.estimatedThresholdHR
          ? Math.round(assessment.estimatedThresholdHR / THRESHOLD_HR_TO_MAX_HR_RATIO)
          : undefined),
      }
      setUserProfile(updatedProfile)

      const newestId = Math.max(...activities.map((a) => a.id))
      setLastStravaActivityId(newestId)

      await updateCurrentUser(authToken, {
        currentFTP: updatedProfile.currentFTP,
        maxHeartRate: updatedProfile.maxHeartRate,
        stravaAnalysisComplete: true,
        lastStravaActivityId: newestId,
      })

      if (isIncremental && planUpdates && planUpdates.length > 0) {
        // For new rides, apply targeted plan updates rather than regenerating the whole plan
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

      setStravaAnalysisComplete(true)
      setAnalysisStatus('done')
    } catch (e) {
      setAnalysisError(e instanceof Error ? e.message : 'Analysis failed')
      setAnalysisStatus('error')
    }
  }

  // Fetch all Strava activities. TanStack Query handles caching so the list
  // is not re-requested on every re-render or page navigation.
  const activitiesQueryEnabled = !!stravaConnection && !!authToken && !!userProfile
  const {
    data: stravaActivities = [],
    isError: isActivitiesError,
  } = useQuery({
    queryKey: ['stravaActivities', authToken],
    queryFn: () => getStravaActivities(authToken!),
    enabled: activitiesQueryEnabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })

  // Trigger initial analysis when activities are first loaded
  useEffect(() => {
    if (!stravaConnection || !authToken || !userProfile) return
    if (stravaActivities.length > 0 && !stravaAnalysisComplete) {
      void runAnalysis(stravaActivities)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authToken, stravaConnection, stravaAnalysisComplete, userProfile?.email, stravaActivities])

  // Propagate fetch errors to the analysis status
  useEffect(() => {
    if (isActivitiesError) {
      setAnalysisStatus('error')
      setAnalysisError('Failed to fetch Strava activities')
    }
  }, [isActivitiesError])

  // Poll for new Strava activities every 5 minutes. TanStack Query's
  // refetchInterval replaces the manual setTimeout polling loop.
  const { data: polledNewActivities } = useQuery({
    queryKey: ['newStravaActivities', authToken, lastStravaActivityId],
    queryFn: () => getNewStravaActivities(authToken!, lastStravaActivityId!),
    enabled: !!stravaConnection && !!authToken && stravaAnalysisComplete && lastStravaActivityId !== null,
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

    setNewRidesCount(unprocessed.length)
    // Merge new activities into the main query cache
    queryClient.setQueryData<StravaActivity[]>(['stravaActivities', authToken], (prev = []) => {
      const existingIds = new Set(prev.map((a) => a.id))
      return [...unprocessed.filter((a) => !existingIds.has(a.id)), ...prev]
    })
    void runAnalysis(unprocessed, true).then(() => setNewRidesCount(0))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polledNewActivities])

  return { stravaActivities, analysisStatus, analysisError, newRidesCount }
}
