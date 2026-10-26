import { useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { setPlan } from '../queries/plan'
import { useT } from '../i18n'
import { getIntervalsImportProgress } from '../services/intervals'
import {
  fetchChatHistory,
  fetchCurrentUser,
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  fetchTrainingPlan,
} from '../services/user'
import type { ImportProgress } from '../services/strava'

/** How often an intervals.icu import is asked how far it is. */
export const IMPORT_POLL_MS = 3000
/** Unanswered progress questions in a row after which nobody asks any more:
 * a backend restart loses the progress, and a button would stay disabled. */
export const IMPORT_POLL_MAX_FAILURES = 5

/** Whether an import is still at work: taking rides in, or the coach looking
 * at them (ai-trainer-ops#306). */
export function importBusy(progress: ImportProgress | null | undefined): boolean {
  return progress?.status === 'running' || progress?.status === 'analysing'
}

/**
 * An intervals.icu import, followed one progress question at a time.
 *
 * Started by the cold-start note's button (24 months) and by a connect (six
 * weeks and the coach's one look, ai-trainer-ops#306). Asked about once as a
 * page that shows it appears, so an import still running, or one a restart cut
 * short, shows after a reload as well (ai-trainer-ops#284). The progress lives
 * in the store: the dashboard's own analysis waits while an import runs.
 */
export function useIntervalsImport() {
  const t = useT()
  const {
    authToken,
    intervals,
    progress,
    setProgress,
    setRideMetricsHistory,
    setMetricsHistory,
    setChatHistory,
    setRiderAssessment,
    setIntervalsAnalysisComplete,
    setLastIntervalsActivityId,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      intervals: Boolean(s.intervalsConnection),
      progress: s.intervalsImport,
      setProgress: s.setIntervalsImport,
      setRideMetricsHistory: s.setRideMetricsHistory,
      setMetricsHistory: s.setMetricsHistory,
      setChatHistory: s.setChatHistory,
      setRiderAssessment: s.setRiderAssessment,
      setIntervalsAnalysisComplete: s.setIntervalsAnalysisComplete,
      setLastIntervalsActivityId: s.setLastIntervalsActivityId,
    }))
  )
  const [importError, setImportError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // A question in flight when the page goes away must not schedule the next.
  const alive = useRef(true)
  // One line of questions at a time, however the import came to be followed.
  const following = useRef(false)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  const refreshRides = async (token: string) => {
    const [rides, metrics] = await Promise.all([
      fetchRideMetricsHistory(token),
      fetchMetricsHistory(token),
    ]).catch(() => [null, null] as const)
    if (rides) setRideMetricsHistory(rides)
    if (metrics) setMetricsHistory(metrics)
  }

  // The coach looked: the plan it changed, what it said in the chat, and the
  // assessment it wrote are the server's now (ai-trainer-ops#306).
  const refreshAfterLook = async (token: string) => {
    setIntervalsAnalysisComplete(true)
    const [plan, chat, user] = await Promise.allSettled([
      fetchTrainingPlan(token),
      fetchChatHistory(token),
      fetchCurrentUser(token),
    ])
    if (plan.status === 'fulfilled') setPlan(plan.value)
    if (chat.status === 'fulfilled') setChatHistory(chat.value)
    if (user.status === 'fulfilled') {
      setRiderAssessment(user.value.riderAssessment)
      setLastIntervalsActivityId(user.value.lastIntervalsActivityId)
    }
  }

  const lost = () => {
    setProgress(null)
    setImportError(t('The import stopped reporting its progress. Reload the page to see what arrived.'))
  }

  // One question at a time: the next is asked only after the last answered,
  // so a slow server does not pile them up.
  const follow = (token: string) => {
    if (following.current) return
    following.current = true
    let failures = 0
    const poll = async () => {
      timer.current = null
      try {
        const next = await getIntervalsImportProgress(token)
        failures = 0
        if (next.status === 'idle') {
          // The server knows of no import any more, finished or cut short.
          following.current = false
          lost()
          return
        }
        setProgress(next)
        if (!importBusy(next)) {
          following.current = false
          // What an import stored stays, also when it ended early.
          if (next.imported > 0) await refreshRides(token)
          if (next.analysed) await refreshAfterLook(token)
          return
        }
      } catch {
        // A dropped poll is not a failed import, but a run of them is a
        // progress the server no longer has.
        failures += 1
        if (failures >= IMPORT_POLL_MAX_FAILURES) {
          following.current = false
          lost()
          return
        }
      }
      if (alive.current) timer.current = setTimeout(() => void poll(), IMPORT_POLL_MS)
      else following.current = false
    }
    timer.current = setTimeout(() => void poll(), IMPORT_POLL_MS)
  }

  useEffect(() => {
    if (!authToken || !intervals) return
    let current = true
    getIntervalsImportProgress(authToken)
      .then((found) => {
        if (!current || !alive.current || following.current) return
        if (importBusy(found)) {
          setProgress(found)
          follow(authToken)
        } else if (found.status === 'interrupted') {
          setProgress(found)
        }
      })
      .catch(() => {
        // Not knowing leaves the page as it was before it asked.
      })
    return () => {
      current = false
    }
    // Once per sign-in and connection; `follow` is the same function every render in effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authToken, intervals])

  /** Start an import with *request* and follow it. */
  const start = async (request: (token: string) => Promise<unknown>) => {
    if (!authToken || importBusy(progress)) return
    setImportError('')
    try {
      await request(authToken)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : t('Import failed'))
      return
    }
    setProgress({ status: 'running', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' })
    follow(authToken)
  }

  return { intervals, progress, importError, start }
}

export type IntervalsImport = ReturnType<typeof useIntervalsImport>
