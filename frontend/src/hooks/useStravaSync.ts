import { useEffect, useRef, useState } from 'react'
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

  const [stravaActivities, setStravaActivities] = useState<StravaActivity[]>([])
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle')
  const [analysisError, setAnalysisError] = useState('')
  const [newRidesCount, setNewRidesCount] = useState(0)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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
        const updatedPlan = await generateTrainingPlan(updatedProfile, authToken, assessment)
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

  const checkForNewActivities = async () => {
    if (!authToken || !stravaConnection) return
    try {
      if (lastStravaActivityId !== null) {
        const newActs = await getNewStravaActivities(authToken, lastStravaActivityId)
        if (newActs.length > 0) {
          setNewRidesCount(newActs.length)
          // Merge new activities with existing for display
          setStravaActivities((prev) => {
            const existingIds = new Set(prev.map((a) => a.id))
            return [...newActs.filter((a) => !existingIds.has(a.id)), ...prev]
          })
          // Re-run analysis using only the new activities for efficiency (incremental)
          await runAnalysis(newActs, true)
          setNewRidesCount(0)
        }
      }
    } catch {
      // Polling errors are silent — don't disrupt the user
    }
  }

  useEffect(() => {
    if (!stravaConnection || !authToken || !userProfile) return

    const fetchActivities = async () => {
      try {
        const acts = await getStravaActivities(authToken)
        setStravaActivities(acts)

        if (!stravaAnalysisComplete && acts.length > 0) {
          await runAnalysis(acts)
        }
      } catch {
        setAnalysisStatus('error')
        setAnalysisError('Failed to fetch Strava activities')
      }
    }
    void fetchActivities()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authToken, stravaConnection, stravaAnalysisComplete, userProfile?.email])

  // Polling: check for new Strava activities every 5 minutes
  useEffect(() => {
    if (!stravaConnection || !authToken || !stravaAnalysisComplete) return

    const schedule = () => {
      pollTimerRef.current = setTimeout(async () => {
        await checkForNewActivities()
        schedule()
      }, POLL_INTERVAL_MS)
    }
    schedule()
    return () => {
      if (pollTimerRef.current !== null) clearTimeout(pollTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authToken, stravaConnection, stravaAnalysisComplete, lastStravaActivityId])

  return { stravaActivities, analysisStatus, analysisError, newRidesCount }
}
