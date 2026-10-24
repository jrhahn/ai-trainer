import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'
import { localeTag, useLanguage, useT } from '../i18n'
import FitFileUpload, { MAX_FILES_PER_UPLOAD } from './FitFileUpload'
import { button, card, cardTitle, dotted, note } from './ui'
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
 * count only, so it cannot say anything the app does not know. It lists what
 * was used and nothing that is missing (ai-trainer-ops#205).
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
  const countId = useId()
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
  ].filter((item): item is string => Boolean(item))

  // One card in the page's two text sizes: no icon tile, and what was used as
  // one grey line instead of a row of tags (owner's review, 2026-10-24).
  return (
    <section className={`${card} space-y-3 text-sm text-gray-700`}>
      <div>
        <h2 className={cardTitle}>{t('Your plan starts from your answers')}</h2>
        <div className="mt-1.5 flex items-center gap-3">
          <div
            role="progressbar"
            aria-labelledby={countId}
            aria-valuemin={0}
            aria-valuemax={COLD_START_ACTIVITIES}
            aria-valuenow={activities}
            className="h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-gray-100"
          >
            <div
              className="h-full rounded-full bg-amber-400"
              style={{ width: `${(activities / COLD_START_ACTIVITIES) * 100}%` }}
            />
          </div>
          <span id={countId} className="shrink-0 text-gray-500">
            {t('{count} of {total} activities', { count: activities, total: COLD_START_ACTIVITIES })}
          </span>
        </div>
      </div>
      <div className="text-gray-500">
        {t('Used')}:{' '}
        <ul className="inline">
          {used.map((item) => (
            <li key={item} className={`inline ${dotted}`}>
              {item}
            </li>
          ))}
        </ul>
      </div>
      {activities === 0 && (
        <p>
          {t(
            'For the first week the coach keeps it moderate: mostly easy endurance, at most one harder session. Every activity you sync or log replaces a guess with a measurement.',
          )}
        </p>
      )}
      <HistoryActions />
    </section>
  )
}

/** How often the intervals.icu import is asked how far it is. */
export const IMPORT_POLL_MS = 3000
/** Unanswered progress questions in a row after which the note stops asking:
 * a backend restart loses the progress, and the button would stay disabled. */
export const IMPORT_POLL_MAX_FAILURES = 5
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
  const { authToken, intervals, strava, stravaAvailable, setRideMetricsHistory, setMetricsHistory } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      intervals: Boolean(s.intervalsConnection),
      strava: Boolean(s.stravaConnection),
      // Off on production (#991): there the note offers intervals.icu alone.
      stravaAvailable: s.stravaAvailable,
      setRideMetricsHistory: s.setRideMetricsHistory,
      setMetricsHistory: s.setMetricsHistory,
    }))
  )
  const [showUpload, setShowUpload] = useState(false)
  const [progress, setProgress] = useState<ImportProgress | null>(null)
  const [importError, setImportError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // A question in flight when the note goes away must not schedule the next.
  const alive = useRef(true)

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

  const importHistory = async () => {
    if (!authToken || progress?.status === 'running') return
    setImportError('')
    try {
      await triggerIntervalsHistoryImport(authToken, HISTORY_MONTHS)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : t('Import failed'))
      return
    }
    setProgress({ status: 'running', total: 0, processed: 0, imported: 0, skipped: 0, failedActivities: [], error: '' })
    // One question at a time: the next is asked only after the last answered,
    // so a slow server does not pile them up.
    let failures = 0
    const poll = async () => {
      timer.current = null
      try {
        const next = await getIntervalsImportProgress(authToken)
        failures = 0
        setProgress(next)
        if (next.status === 'done' || next.status === 'error') {
          if (next.imported > 0) await refreshRides(authToken)
          return
        }
      } catch {
        // A dropped poll is not a failed import, but a run of them is a
        // progress the server no longer has.
        failures += 1
        if (failures >= IMPORT_POLL_MAX_FAILURES) {
          setProgress(null)
          setImportError(t('The import stopped reporting its progress. Reload the page to see what arrived.'))
          return
        }
      }
      if (alive.current) timer.current = setTimeout(() => void poll(), IMPORT_POLL_MS)
    }
    timer.current = setTimeout(() => void poll(), IMPORT_POLL_MS)
  }

  return (
    <div className="space-y-2 border-t border-gray-100 pt-3">
      <p className="font-medium text-gray-900">{t('Give your coach your history')}</p>
      <p>
        {t('Rides from before you signed up count from day one: your plan then starts from what you actually did.')}
      </p>
      <div className="flex flex-wrap gap-2">
        {intervals && (
          <button
            type="button"
            onClick={() => void importHistory()}
            disabled={progress?.status === 'running'}
            className={button}
          >
            {t('Import my last {months} months from intervals.icu', { months: HISTORY_MONTHS })}
          </button>
        )}
        {!intervals && !strava && (
          <Link to="/settings#settings-activities" className={button}>
            {stravaAvailable ? t('Connect intervals.icu or Strava') : t('Connect intervals.icu')}
          </Link>
        )}
        <button
          type="button"
          onClick={() => setShowUpload((open) => !open)}
          aria-expanded={showUpload}
          className={button}
        >
          {t('Upload past rides')}
        </button>
      </div>
      {!intervals && !strava && (
        <p className="text-gray-500">
          {stravaAvailable
            ? t('Strava brings in your last 24 months by itself once connected.')
            : t('On Strava? Connect your Garmin, Wahoo or other device to intervals.icu (free) as well: rides that come only through Strava do not reach us.')}
        </p>
      )}
      {progress?.status === 'running' && (
        <p role="status" className={note(true)}>
          {progress.total > 0
            ? t('Importing… {processed} of {total} activities', { processed: progress.processed, total: progress.total })
            : t('Importing…')}
        </p>
      )}
      {progress?.status === 'done' && (
        <p role="status" className={note(true)}>
          {t('{n} imported', { n: progress.imported })}
        </p>
      )}
      {(progress?.status === 'error' || importError) && (
        <p role="alert" className={note(false)}>
          {importError || progress?.error || t('Import failed')}
        </p>
      )}
      {showUpload && (
        <div className="space-y-2 pt-1 text-gray-500">
          <p>
            {t('Garmin Connect: open a ride, then the gear menu, then “Export Original”. That gives a .zip with the .fit inside; upload it as it is.')}
          </p>
          <p>
            {t('Wahoo, Zwift and most bike computers save .fit files you can upload directly. Up to {n} files at a time; a larger selection is sent in parts.', { n: MAX_FILES_PER_UPLOAD })}
          </p>
          <FitFileUpload embedded />
        </div>
      )}
    </div>
  )
}
