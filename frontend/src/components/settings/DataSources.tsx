import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../../store/useAppStore'
import { useAuthToken } from '../../hooks/useAuthToken'
import { useNote } from '../../hooks/useProfileSave'
import { resolveActiveSource } from '../../hooks/useStravaSync'
import { useImportProgress } from '../../hooks/useImportProgress'
import { updateCurrentUser } from '../../services/user'
import { useLanguage, useT } from '../../i18n'
import { setupGuideSection } from '../../utils/links'
import FitFileUpload from '../FitFileUpload'
import IntervalsConnect from '../IntervalsConnect'
import SetupGuideLink from '../SetupGuideLink'
import StravaConnect from '../StravaConnect'
import StravaImportSummary from '../StravaImportSummary'
import { note } from '../ui'

/** Settings → Activities: where the rides come from, the automatic syncs, FIT
 *  files and the running import (ai-trainer-ops#274: out of `SettingsPage`). */
export default function DataSources() {
  const t = useT()
  const language = useLanguage()
  const authToken = useAuthToken()
  const {
    stravaConnection,
    stravaAvailable,
    intervalsConnection,
    lastStravaActivityId,
    stravaAutoSyncEnabled,
    lastIntervalsActivityId,
    intervalsAutoSyncEnabled,
    isExpertMode,
    setStravaAutoSyncEnabled,
    setIntervalsAutoSyncEnabled,
  } = useAppStore(
    useShallow((s) => ({
      stravaConnection: s.stravaConnection,
      stravaAvailable: s.stravaAvailable,
      intervalsConnection: s.intervalsConnection,
      lastStravaActivityId: s.lastStravaActivityId,
      stravaAutoSyncEnabled: s.stravaAutoSyncEnabled,
      lastIntervalsActivityId: s.lastIntervalsActivityId,
      intervalsAutoSyncEnabled: s.intervalsAutoSyncEnabled,
      isExpertMode: s.isExpertMode,
      setStravaAutoSyncEnabled: s.setStravaAutoSyncEnabled,
      setIntervalsAutoSyncEnabled: s.setIntervalsAutoSyncEnabled,
    })),
  )
  // Which source is actually being read, from the same function the sync hook
  // uses (ai-trainer-ops#49). Recomputing the precedence here would let this
  // sentence and the sync disagree, and an athlete told the wrong thing about
  // where their data comes from has no way to find out otherwise.
  const activeSource = resolveActiveSource({
    intervalsConnection,
    intervalsAutoSyncEnabled,
    stravaConnection,
    stravaAutoSyncEnabled,
  })
  const bothConnected = Boolean(intervalsConnection && stravaConnection)
  const [stravaSyncMsg, showStravaSyncMsg] = useNote()
  const [stravaSyncSaving, setStravaSyncSaving] = useState(false)
  const [intervalsSyncMsg, showIntervalsSyncMsg] = useNote()
  const [intervalsSyncSaving, setIntervalsSyncSaving] = useState(false)
  const importProgress = useImportProgress()

  const handleStravaAutoSyncChange = async (enabled: boolean) => {
    if (!authToken) return
    const previous = stravaAutoSyncEnabled
    setStravaAutoSyncEnabled(enabled)
    setStravaSyncSaving(true)
    showStravaSyncMsg(null)
    try {
      await updateCurrentUser(authToken, { stravaAutoSyncEnabled: enabled })
      showStravaSyncMsg(
        {
          type: 'success',
          text: enabled ? t('Automatic Strava sync enabled.') : t('Automatic Strava sync disabled.'),
        },
        3000,
      )
    } catch {
      setStravaAutoSyncEnabled(previous)
      showStravaSyncMsg({ type: 'error', text: t('Could not save Strava sync setting. Please try again.') })
    } finally {
      setStravaSyncSaving(false)
    }
  }

  const handleIntervalsAutoSyncChange = async (enabled: boolean) => {
    if (!authToken) return
    const previous = intervalsAutoSyncEnabled
    setIntervalsAutoSyncEnabled(enabled)
    setIntervalsSyncSaving(true)
    showIntervalsSyncMsg(null)
    try {
      await updateCurrentUser(authToken, { intervalsAutoSyncEnabled: enabled })
      showIntervalsSyncMsg(
        {
          type: 'success',
          text: enabled ? t('Automatic Intervals.icu sync enabled.') : t('Automatic Intervals.icu sync disabled.'),
        },
        3000,
      )
    } catch {
      setIntervalsAutoSyncEnabled(previous)
      showIntervalsSyncMsg({ type: 'error', text: t('Could not save Intervals.icu sync setting. Please try again.') })
    } finally {
      setIntervalsSyncSaving(false)
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Data Sources')}</h3>

      {/* One plain line, no tinted box: the section had six text styles and
          four coloured boxes (owner's review, 2026-10-23: "einheitliches
          sauberes Design, eher minimalistisch"). */}
      <p className="text-sm text-gray-500">
          {/* The backend's URL used to be printed here (ai-trainer-ops#50).
              It is a deployment detail no athlete can act on, and on a local
              install it read "the backend at http://localhost:8000" — which
              tells a cyclist nothing and makes the product look unfinished.

              "at rest" and not "never leave this server", which is what this
              said for one commit until review caught it. The keys do leave:
              the intervals.icu key goes to intervals.icu, the Strava token to
              Strava, the athlete's own provider key to OpenAI or Gemini on
              every call. A privacy sentence on a settings page is something
              an athlete relies on, so it may only claim what
              `EncryptedString` actually provides.

              The Strava sentence said a paid Strava subscription might be
              needed. What stops testers is that intervals.icu does not pass
              on rides it got only from Strava (owner's review, 2026-10-23). */}
          {t('Your rides come in through intervals.icu or as files (.fit, .gpx, .tcx). Rides that reach intervals.icu only through Strava do not reach us. Keys are stored encrypted at rest.')}
      </p>
      <p className="mt-2">
        <SetupGuideLink
          href={setupGuideSection(language, 'intervals')}
          label={t('How your rides get in through intervals.icu')}
        />
      </p>

      <div className="mt-2 divide-y divide-gray-100">
        {/* intervals.icu first (ai-trainer-ops#20): the primary path, with
            Strava as a convenience on top. */}
        <div className="py-4 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-gray-900">Intervals.icu</p>
            </div>
            {/* The state is in the block below; which source is read only
                matters with both connected (ai-trainer-ops#49). */}
            {bothConnected && activeSource === 'intervals' && (
              <span className="shrink-0 text-sm text-gray-500">{t('Active source')}</span>
            )}
          </div>
          <IntervalsConnect />
          {/* No "Automatic sync: On" line: the switch below says it (owner's
              review, 2026-10-23). */}
          {isExpertMode && (
            <p className="text-sm text-gray-500">
              {t('Last sync cursor: {cursor}', { cursor: lastIntervalsActivityId ?? t('none yet') })}
            </p>
          )}
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm text-gray-900">{t('Turn on automatic sync with Intervals.icu')}</p>
              {intervalsSyncMsg && (
                <p className={`mt-2 ${note(intervalsSyncMsg.type === 'success')}`}>
                  {intervalsSyncMsg.text}
                </p>
              )}
            </div>
            <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={intervalsAutoSyncEnabled}
                disabled={intervalsSyncSaving}
                aria-label={t('Turn on automatic sync with Intervals.icu')}
                onChange={(e) => void handleIntervalsAutoSyncChange(e.target.checked)}
              />
              <span className="h-6 w-11 rounded-full bg-gray-200 transition peer-checked:bg-amber-500 peer-disabled:opacity-50" />
              <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white shadow transition peer-checked:translate-x-5" />
            </label>
          </div>
        </div>

        {/* Off on production (owner's decision, 2026-10-21): athletes connect
            Strava to intervals.icu instead. An existing connection stays
            visible, so it can still be disconnected. */}
        {(stravaAvailable || stravaConnection) && (
          <div className="py-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-gray-900">Strava</p>
              </div>
              {bothConnected && activeSource === 'strava' && (
                <span className="shrink-0 text-sm text-gray-500">{t('Active source')}</span>
              )}
            </div>
            {/* Said once, where an athlete with both connections will look for
                it: the badge above says which one is read, and this says why
                the other one is not (ai-trainer-ops#49). Without it, two
                connected sources and a sync from one read as a bug. */}
            {activeSource === 'intervals' && stravaConnection && (
              <p className="text-sm text-gray-500">
                {t('Intervals.icu is the primary source, so activities are read from there. Strava stays connected and is used if you turn off Intervals.icu sync.')}
              </p>
            )}
            <StravaConnect />
            {isExpertMode && (
              <p className="text-sm text-gray-500">
                {t('Last sync cursor: {cursor}', { cursor: lastStravaActivityId ?? t('none yet') })}
              </p>
            )}
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm text-gray-900">{t('Turn on automatic sync with Strava')}</p>
                {stravaSyncMsg && (
                  <p className={`mt-2 ${note(stravaSyncMsg.type === 'success')}`}>
                    {stravaSyncMsg.text}
                  </p>
                )}
              </div>
              <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
                <input
                  type="checkbox"
                  className="peer sr-only"
                  checked={stravaAutoSyncEnabled}
                  disabled={stravaSyncSaving}
                  aria-label={t('Turn on automatic sync with Strava')}
                  onChange={(e) => void handleStravaAutoSyncChange(e.target.checked)}
                />
                <span className="h-6 w-11 rounded-full bg-gray-200 transition peer-checked:bg-amber-500 peer-disabled:opacity-50" />
                <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white shadow transition peer-checked:translate-x-5" />
              </label>
            </div>
          </div>
        )}

        <div className="pt-4 space-y-1">
          {/* Not "FIT files": GPX and TCX come in too (ai-trainer-ops#311). */}
          <p className="text-sm font-semibold text-gray-900">{t('Upload files')}</p>
          <FitFileUpload embedded />
        </div>
      </div>

      {importProgress.status !== 'idle' && (
        <div className="mt-4 border border-gray-100 rounded-xl p-4 bg-gray-50">
          <p className="text-sm font-medium text-gray-700 uppercase tracking-wider mb-2">{t('Strava Activity Analysis')}</p>
          {importProgress.status === 'running' && (() => {
            const pct = importProgress.total > 0
              ? Math.round((importProgress.processed / importProgress.total) * 100)
              : null
            return (
              <>
                <div className="w-full bg-gray-200 rounded-full h-2 mb-1">
                  <div
                    className="bg-amber-500 h-2 rounded-full transition-all duration-300"
                    style={{ width: pct !== null ? `${pct}%` : '10%' }}
                  />
                </div>
                <p className="text-sm text-gray-500">
                  {pct !== null
                    ? t('Processed activities: {processed} / {total} ({pct}%) · {imported} imported', {
                        processed: importProgress.processed,
                        total: importProgress.total,
                        pct,
                        imported: importProgress.imported,
                      })
                    : t('Fetching activity list…')}
                </p>
              </>
            )
          })()}
          {importProgress.status === 'done' && (
            <div className="space-y-3">
              <p className="text-sm text-gray-700">
                {t('{imported} imported, {skipped} skipped', { imported: importProgress.imported, skipped: importProgress.skipped })}
              </p>
              <StravaImportSummary progress={importProgress} compact />
            </div>
          )}
          {importProgress.status === 'error' && (
            <p className="text-sm text-red-600">{t('Import failed: {error}', { error: importProgress.error || t('unknown error') })}</p>
          )}
        </div>
      )}
    </div>
  )
}
