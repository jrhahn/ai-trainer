import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { History, Link2, Sprout, Upload } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'
import { localeTag, useLanguage, useT } from '../i18n'
import FitFileUpload, { MAX_FILES_PER_UPLOAD } from './FitFileUpload'
import { getIntervalsImportProgress, triggerIntervalsHistoryImport } from '../services/intervals'
import { fetchMetricsHistory, fetchRideMetricsHistory } from '../services/user'
import type { ImportProgress } from '../services/strava'

/** Activities after which the backend's own models start reading the athlete
 * (`INSIGHT_MIN_ACTIVITIES`, `PREDICTION_MIN_ACTIVITIES` and the rest are 8). */
export const COLD_START_ACTIVITIES = 8

/** What a plan without rides was built from (ai-trainer-ops#89).
 *
 * A tester judges the app on the first plan, and without synced rides that plan
 * comes from four onboarding answers. Built from the profile and the activity
 * count only, so it cannot say anything the app does not know.
 */
export default function ColdStartNote() {
  const { profile, activities, loadFailed } = useAppStore(
    useShallow((s) => ({
      profile: s.userProfile,
      activities: s.rideMetricsHistory.length,
      loadFailed: Boolean(s.dataLoadWarning),
    }))
  )
  const t = useT()
  const language = useLanguage()
  // A failed load leaves the ride history empty, and the note would then tell
  // an athlete with rides that nothing is known about them.
  if (!profile || loadFailed || activities >= COLD_START_ACTIVITIES) return null

  const goal =
    profile.trainingGoal === 'race'
      ? profile.raceDate
        ? t('a race on {date}', {
            date: parseLocalDate(profile.raceDate).toLocaleDateString(localeTag(language), { day: 'numeric', month: 'short' }),
          })
        : t('a race')
      : t('general fitness')
  const used = [
    goal,
    profile.weeklyHours != null && t('{hours} h a week', { hours: profile.weeklyHours }),
    profile.trainingDays?.length &&
      profile.trainingDays.map((d) => t(d[0].toUpperCase() + d.slice(1, 3))).join(', '),
    t('{level} level', { level: t(profile.fitnessLevel) }),
    profile.currentFTP && `FTP ${profile.currentFTP} W`,
  ].filter(Boolean)
  const unknown = [
    !profile.currentFTP && t('your FTP'),
    t('your recent training load'),
    t('how you respond to sessions'),
  ].filter(
    Boolean
  )

  return (
    <section className="flex gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-gray-700">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
        <Sprout size={16} aria-hidden="true" />
      </span>
      <div className="space-y-1">
        <h2 className="font-semibold text-gray-900">{t('Your plan starts from your answers')}</h2>
        <p>{t('It used: {items}.', { items: used.join(', ') })}</p>
        <p>{t('It does not know yet: {items}.', { items: unknown.join(', ') })}</p>
        {activities === 0 ? (
          <p>
            {t(
              'So the coach is told to keep the first week moderate: mostly easy endurance, at most one harder session. Every activity you sync or log replaces a guess with a measurement.',
            )}
          </p>
        ) : (
          <p>
            {t('{count} of {total} activities so far. Every one replaces a guess with a measurement.', {
              count: activities,
              total: COLD_START_ACTIVITIES,
            })}
          </p>
        )}
        <p className="text-gray-500">
          {t('This note goes away after {total} activities.', { total: COLD_START_ACTIVITIES })}
        </p>
        <HistoryActions />
      </div>
    </section>
  )
}

/** How often the intervals.icu import is asked how far it is. */
export const IMPORT_POLL_MS = 3000
/** The most the backend imports in one go (`routers/intervals.py` clamps to 24). */
export const HISTORY_MONTHS = 24

/** Ways to hand the coach a history before the first ride (ai-trainer-ops#157).
 *
 * Every one of them existed and none was where a new athlete looks: the upload
 * sat in Settings, and the intervals.icu history import had no button at all.
 * Rides from before sign-up are what turns the guesses above into
 * measurements soonest.
 */
function HistoryActions() {
  const t = useT()
  const { authToken, intervals, strava, setRideMetricsHistory, setMetricsHistory } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      intervals: Boolean(s.intervalsConnection),
      strava: Boolean(s.stravaConnection),
      setRideMetricsHistory: s.setRideMetricsHistory,
      setMetricsHistory: s.setMetricsHistory,
    }))
  )
  const [showUpload, setShowUpload] = useState(false)
  const [progress, setProgress] = useState<ImportProgress | null>(null)
  const [importError, setImportError] = useState('')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current)
    },
    []
  )

  const refreshRides = async (token: string) => {
    const [rides, metrics] = await Promise.all([
      fetchRideMetricsHistory(token),
      fetchMetricsHistory(token),
    ]).catch(() => [null, null] as const)
    if (rides) setRideMetricsHistory(rides)
    if (metrics) setMetricsHistory(metrics)
  }

  const importHistory = async () => {
    if (!authToken || progress?.status === 'running') return
    setImportError('')
    try {
      await triggerIntervalsHistoryImport(authToken, HISTORY_MONTHS)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
      return
    }
    setProgress({ status: 'running', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' })
    const poll = async () => {
      try {
        const next = await getIntervalsImportProgress(authToken)
        setProgress(next)
        if (next.status === 'done' || next.status === 'error') {
          if (timer.current) clearInterval(timer.current)
          timer.current = null
          if (next.imported > 0) await refreshRides(authToken)
        }
      } catch {
        // The next tick asks again; a dropped poll is not a failed import.
      }
    }
    timer.current = setInterval(() => void poll(), IMPORT_POLL_MS)
  }

  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
      <p className="font-medium text-gray-900">{t('Give your coach your history')}</p>
      <p className="text-gray-600">
        {t('Rides from before you signed up count from day one: your plan then starts from what you actually did.')}
      </p>
      <div className="flex flex-wrap gap-2">
        {intervals && (
          <button
            type="button"
            onClick={() => void importHistory()}
            disabled={progress?.status === 'running'}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-gray-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <History size={14} aria-hidden="true" />
            {t('Import my last {months} months from intervals.icu', { months: HISTORY_MONTHS })}
          </button>
        )}
        {!intervals && !strava && (
          <Link
            to="/settings#settings-activities"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-gray-700 hover:bg-slate-50"
          >
            <Link2 size={14} aria-hidden="true" />
            {t('Connect intervals.icu or Strava')}
          </Link>
        )}
        <button
          type="button"
          onClick={() => setShowUpload((open) => !open)}
          aria-expanded={showUpload}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 font-medium text-gray-700 hover:bg-slate-50"
        >
          <Upload size={14} aria-hidden="true" />
          {t('Upload past rides')}
        </button>
      </div>
      {!intervals && !strava && (
        <p className="text-xs text-gray-500">
          {t('Strava brings in your last 24 months by itself once connected.')}
        </p>
      )}
      {progress?.status === 'running' && (
        <p role="status" className="text-xs text-gray-600">
          {progress.total > 0
            ? t('Importing… {processed} of {total} activities', { processed: progress.processed, total: progress.total })
            : t('Importing…')}
        </p>
      )}
      {progress?.status === 'done' && (
        <p role="status" className="text-xs text-green-700">
          {t('{n} imported', { n: progress.imported })}
        </p>
      )}
      {(progress?.status === 'error' || importError) && (
        <p role="alert" className="text-xs text-red-700">
          {t(importError || progress?.error || 'Import failed')}
        </p>
      )}
      {showUpload && (
        <div className="space-y-2 rounded-lg bg-slate-50 p-3">
          <p className="text-xs text-gray-600">
            {t('Garmin Connect: open a ride, then the gear menu, then “Export Original”. That gives a .zip with the .fit inside; upload it as it is.')}
          </p>
          <p className="text-xs text-gray-600">
            {t('Wahoo, Zwift and most bike computers save .fit files you can upload directly. Up to {n} files at a time; a larger selection is sent in parts.', { n: MAX_FILES_PER_UPLOAD })}
          </p>
          <FitFileUpload embedded />
        </div>
      )}
    </div>
  )
}
