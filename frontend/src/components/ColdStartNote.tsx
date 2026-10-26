import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { useProfile } from '../queries/profile'
import { parseLocalDate } from '../utils/workout'
import { localeTag, useLanguage, useT } from '../i18n'
import FitFileUpload, { MAX_FILES_PER_UPLOAD } from './FitFileUpload'
import { button, card, cardTitle, dotted } from './ui'
import { triggerIntervalsHistoryImport } from '../services/intervals'
import { importBusy, useIntervalsImport, type IntervalsImport } from '../hooks/useIntervalsImport'
import IntervalsImportStatus from './IntervalsImportStatus'

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
  const profile = useProfile()
  const { activities, loadFailed } = useAppStore(
    useShallow((s) => ({
      activities: s.rideMetricsHistory.length,
      loadFailed: Boolean(s.dataLoadWarning),
    }))
  )
  const t = useT()
  const language = useLanguage()
  const countId = useId()
  const historyImport = useIntervalsImport()
  // A failed load leaves the ride history empty, and the note would then tell
  // an athlete with rides that nothing is known about them.
  if (!profile || loadFailed) return null
  if (activities >= COLD_START_ACTIVITIES) {
    // The note has done its job, but an import still running or cut short stays
    // in view: the rides it stored before a reload already count (ai-trainer-ops#284).
    return importBusy(historyImport.progress) || historyImport.progress?.status === 'interrupted' ? (
      <section className={`${card} space-y-2 text-sm text-gray-700`}>
        <h2 className={cardTitle}>{t('Your history from intervals.icu')}</h2>
        <ImportButton historyImport={historyImport} />
        <IntervalsImportStatus intervalsImport={historyImport} />
      </section>
    ) : null
  }

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
    profile.fitnessLevel && t('{level} level', { level: t(profile.fitnessLevel) }),
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
        {t('Based on')}:{' '}
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
      <HistoryActions historyImport={historyImport} />
    </section>
  )
}

/** The most the backend imports in one go (`routers/intervals.py` clamps to 24). */
export const HISTORY_MONTHS = 24

function ImportButton({ historyImport }: { historyImport: IntervalsImport }) {
  const t = useT()
  if (!historyImport.intervals) return null
  return (
    <button
      type="button"
      onClick={() => void historyImport.start((token) => triggerIntervalsHistoryImport(token, HISTORY_MONTHS))}
      disabled={importBusy(historyImport.progress)}
      className={button}
    >
      {t('Import my last {months} months from intervals.icu', { months: HISTORY_MONTHS })}
    </button>
  )
}

/** Ways to hand the coach a history before the first ride (ai-trainer-ops#157).
 *
 * Every one of them existed and none was where a new athlete looks: the upload
 * sat in Settings, and the intervals.icu history import had no button at all.
 * Rides from before sign-up are what turns the guesses above into
 * measurements soonest.
 */
function HistoryActions({ historyImport }: { historyImport: IntervalsImport }) {
  const t = useT()
  const { intervals, strava, stravaAvailable } = useAppStore(
    useShallow((s) => ({
      intervals: Boolean(s.intervalsConnection),
      strava: Boolean(s.stravaConnection),
      // Off on production (#991): there the note offers intervals.icu alone.
      stravaAvailable: s.stravaAvailable,
    }))
  )
  const [showUpload, setShowUpload] = useState(false)

  return (
    <div className="space-y-2 border-t border-gray-100 pt-3">
      <p className="font-medium text-gray-900">{t('Give your coach your history')}</p>
      <p>
        {t('Rides from before you signed up count from day one: your plan then starts from what you actually did.')}
      </p>
      <div className="flex flex-wrap gap-2">
        <ImportButton historyImport={historyImport} />
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
      <IntervalsImportStatus intervalsImport={historyImport} />
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
