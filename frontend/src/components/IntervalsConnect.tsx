import { useState } from 'react'
import { Link2Off, Save } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { slots, useT, type Translate } from '../i18n'
import {
  checkIntervals,
  disconnectIntervals,
  saveIntervalsConnection,
  startIntervalsConnectImport,
  type IntervalsCheck,
} from '../services/intervals'
import { useIntervalsImport } from '../hooks/useIntervalsImport'
import IntervalsImportStatus from './IntervalsImportStatus'
import KeyRevealButton, { KEY_MASK } from './KeyRevealButton'
import { note } from './ui'

// Where intervals.icu shows the API key, under Developer Settings.
const INTERVALS_SETTINGS_URL = 'https://intervals.icu/settings'
// What the first read after connecting found. Saving already refuses a key
// intervals.icu does not take (ai-trainer-ops#212); this read counts what
// arrives, and still tells a key refused since (403) from an outage the
// athlete cannot fix by re-copying the key (ai-trainer-ops#88).
//
// A working key is not yet rides reaching the coach: intervals.icu may not pass
// on what it got from Strava (owner, 2026-10-21), so the check counts both.
type KeyCheck = IntervalsCheck | 'rejected' | 'unreachable' | null

function keyCheckText(check: IntervalsCheck, t: Translate) {
  if (check.usable === 0) {
    return check.fromStrava > 0
      ? t('The key works, but none of your rides from the last month can reach the coach yet.')
      : t('The key works, but there are no activities from the last month yet.')
  }
  // One is singular, in German too: "1 Aktivitäten" read wrong (ai-trainer-ops#121).
  if (check.usable === 1) return t('The key works: 1 activity from the last month found.')
  return t('The key works: {count} activities from the last month found.', { count: check.usable })
}

/** Where a ride has to come from so intervals.icu may pass it on. */
function StravaWithheld({ count }: { count: number }) {
  const t = useT()
  return (
    <div role="status" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
      <p className="font-semibold">
        {count === 1
          ? t('1 ride from the last month came to intervals.icu from Strava.')
          : t('{count} rides from the last month came to intervals.icu from Strava.', { count })}{' '}
        {t('Strava does not let intervals.icu pass those on, so the coach cannot see them.')}
      </p>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        <li>{t('New rides: in your intervals.icu settings, connect the device or app that records them (Garmin, Wahoo, Zwift, COROS, Suunto …). Strava can stay connected.')}</li>
        <li>{t('Older rides: request your archive from Strava (Settings → My Account → Download or Delete Your Account), then upload it to intervals.icu.')}</li>
      </ul>
    </div>
  )
}

/** `onChange` runs after a connect or a disconnect, so a page can drop what it
 *  said about the old connection: onboarding's analysis error (ai-trainer-ops#212). */
export default function IntervalsConnect({ onChange }: { onChange?: () => void } = {}) {
  const authToken = useAppStore((s) => s.authToken)
  const intervalsConnection = useAppStore((s) => s.intervalsConnection)
  const setIntervalsConnection = useAppStore((s) => s.setIntervalsConnection)
  const setIntervalsAnalysisComplete = useAppStore((s) => s.setIntervalsAnalysisComplete)
  const setIntervalsImport = useAppStore((s) => s.setIntervalsImport)

  const [apiKey, setApiKey] = useState('')
  // Blank, not '0' (ai-trainer-ops#50). '0' is the backend's word for "the
  // athlete this key belongs to", which is the right default and a number no
  // athlete has any way to know. It is still what gets sent; the field just
  // stops asserting it as though the reader chose it.
  const [athleteId, setAthleteId] = useState(intervalsConnection?.athleteId ?? '')
  const [athleteName, setAthleteName] = useState(intervalsConnection?.athleteName ?? '')
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [keyCheck, setKeyCheck] = useState<KeyCheck>(null)
  const [keyShown, setKeyShown] = useState(false)
  const connectImport = useIntervalsImport()
  const t = useT()

  const handleConnect = async () => {
    if (!authToken) return
    const trimmedKey = apiKey.trim()
    if (!trimmedKey) {
      setError('Enter an Intervals.icu API key.')
      return
    }

    setIsSaving(true)
    setError(null)
    try {
      const connection = await saveIntervalsConnection(authToken, {
        apiKey: trimmedKey,
        athleteId: athleteId.trim() || '0',
        athleteName: athleteName.trim(),
      })
      setIntervalsConnection(connection)
      setIntervalsAnalysisComplete(false)
      onChange?.()
      setApiKey('')
      setAthleteId(connection.athleteId)
      setAthleteName(connection.athleteName ?? '')
      try {
        const check = await checkIntervals(authToken)
        setKeyCheck(check)
        // A key that finds rides takes the last six weeks in at once, and the
        // coach looks at them: connecting used to change nothing anyone could
        // see (ai-trainer-ops#306).
        if (check.usable > 0) void connectImport.start(startIntervalsConnectImport)
      } catch (err: unknown) {
        const status = (err as { status?: number } | null)?.status
        setKeyCheck(status === 403 ? 'rejected' : 'unreachable')
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      console.error('[IntervalsConnect] Failed to save Intervals.icu credentials:', message, err)
      // A refused key is kept out, and the form stays open with the key in it.
      setError(
        (err as { status?: number } | null)?.status === 403
          ? 'intervals.icu did not accept this key. Copy the whole API key again and paste it here.'
          : 'Could not save the Intervals.icu connection. Please check the credentials and try again.'
      )
    } finally {
      setIsSaving(false)
    }
  }

  const handleDisconnect = async () => {
    if (!authToken) return
    setIsSaving(true)
    setError(null)
    try {
      await disconnectIntervals(authToken)
      setIntervalsConnection(null)
      setIntervalsAnalysisComplete(false)
      // The old connection's import is not the next one's (review of #1178).
      setIntervalsImport(null)
      onChange?.()
      setKeyCheck(null)
      setAthleteId('0')
      setAthleteName('')
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      console.error('[IntervalsConnect] Failed to disconnect Intervals.icu:', message, err)
      setError('Could not disconnect Intervals.icu. Please try again.')
    } finally {
      setIsSaving(false)
    }
  }

  if (intervalsConnection) {
    return (
      // A row, not a green box: one tick says it is connected (owner's review,
      // 2026-10-23, "eher minimalistisch").
      <div className="space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-0.5">
            <p className="text-sm text-gray-900">{t('Connected to Intervals.icu')}</p>
            {/* A name if the athlete gave one, never the ID: the key names the
                account, and "Konto 0" or "Konto i600858" told nobody anything
                (owner's review, 2026-10-23). */}
            {intervalsConnection.athleteName && (
              <p className="text-sm text-gray-500">{intervalsConnection.athleteName}</p>
            )}
            {/* The key as dots, and its hint while the eye is held: the same
                rule as the AI key (ai-trainer-ops#307). */}
            <div className="flex items-center gap-1.5 text-sm text-gray-500">
              <span>{t('API Key')}</span>
              <span className="font-mono" data-testid="intervals-key">
                {keyShown && intervalsConnection.keyHint ? intervalsConnection.keyHint : KEY_MASK}
              </span>
              {intervalsConnection.keyHint && (
                <KeyRevealButton
                  shown={keyShown}
                  onShow={setKeyShown}
                  className="inline-flex min-h-10 min-w-10 items-center justify-center text-gray-500 hover:text-gray-800"
                />
              )}
            </div>
            {keyCheck && typeof keyCheck === 'object' && (
              <p className="text-sm text-gray-500">{keyCheckText(keyCheck, t)}</p>
            )}
            <IntervalsImportStatus intervalsImport={connectImport} />
            {keyCheck === 'rejected' && (
              <p role="alert" className="text-sm text-red-700">
                {t('intervals.icu did not accept this key. Disconnect, copy the whole API key again and reconnect.')}
              </p>
            )}
            {keyCheck === 'unreachable' && (
              <p role="status" className="text-sm text-amber-700">
                {t('The key is saved, but intervals.icu did not answer just now, so it is not checked yet. Your rides will sync once it is back.')}
              </p>
            )}
          </div>
          <button
            onClick={() => void handleDisconnect()}
            disabled={isSaving}
            className="flex shrink-0 items-center gap-1 text-sm text-gray-500 hover:text-red-600 disabled:opacity-50"
          >
            <Link2Off size={14} aria-hidden="true" />
            {t('Disconnect')}
          </button>
        </div>
        {/* Below the row, full width: it is the part to read and act on. */}
        {keyCheck && typeof keyCheck === 'object' && keyCheck.fromStrava > 0 && (
          <StravaWithheld count={keyCheck.fromStrava} />
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3 border border-gray-200 rounded-xl p-4">
      <div>
        <p className="text-sm font-semibold text-gray-900">{t('Connect Intervals.icu')}</p>
        <ol className="mt-2 list-decimal pl-4 space-y-1 text-sm text-gray-600">
          {/* First, because it is the step that fails silently: a ride that
              reached intervals.icu only through Strava is withheld from its API
              (owner, 2026-10-21). */}
          <li>
            {t('In intervals.icu, connect the device or app that records your rides (Garmin, Wahoo, Zwift, COROS, Suunto …). Strava alone is not enough: intervals.icu may not pass on rides it gets from Strava.')}
          </li>
          {/* intervals.icu's own labels, in the language that site shows: in
              German it says "Entwicklereinstellungen" and "API-Schlüssel"
              (owner's screenshot, 2026-10-23). */}
          <li>
            {slots(t('Open {link} and scroll down to {section}.'), {
              link: (
                <a
                  key="link"
                  href={INTERVALS_SETTINGS_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="font-semibold text-amber-600 hover:text-amber-700 underline"
                >
                  {t('your intervals.icu settings')}
                </a>
              ),
              section: <strong key="section">{t('Developer Settings')}</strong>,
            })}
          </li>
          <li>
            {slots(t('Copy the {field}. If the field is empty, generate one there.'), {
              field: <strong key="field">{t('API Key')}</strong>,
            })}
          </li>
          <li>{t('Paste it below and connect. That is all: the key knows whose account it is.')}</li>
        </ol>
      </div>
      <label className="block text-sm font-medium text-gray-700">
        {t('API Key')}
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
          autoComplete="off"
        />
      </label>
      {/* Only for a key that reaches more than one athlete, such as a coach's. */}
      <details className="text-sm text-gray-600">
        <summary className="cursor-pointer select-none">{t('Athlete ID and name (optional)')}</summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <label className="block text-sm font-medium text-gray-700">
            {t('Athlete ID')}
            <input
              type="text"
              value={athleteId}
              onChange={(e) => setAthleteId(e.target.value)}
              className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('Leave blank for your own account')}
            />
          </label>
          <label className="block text-sm font-medium text-gray-700">
            {t('Athlete Name')}
            <input
              type="text"
              value={athleteName}
              onChange={(e) => setAthleteName(e.target.value)}
              className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="Optional"
            />
          </label>
        </div>
      </details>
      <button
        onClick={() => void handleConnect()}
        disabled={isSaving || !apiKey.trim()}
        className="flex min-h-10 items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
      >
        <Save size={15} />
        {isSaving ? t('Saving...') : t('Connect Intervals.icu')}
      </button>
      {error && (
        <p role="alert" className={note(false)}>
          {t(error)}
        </p>
      )}
    </div>
  )
}
