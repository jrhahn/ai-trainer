import { useState } from 'react'
import { AlertCircle, CheckCircle, Link2Off, Save } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  disconnectIntervals,
  getIntervalsActivities,
  saveIntervalsConnection,
} from '../services/intervals'

// Where intervals.icu shows the API key, under Developer Settings.
const INTERVALS_SETTINGS_URL = 'https://intervals.icu/settings'
// The activities endpoint returns at most this many, so more reads as "or more".
const ACTIVITY_SAMPLE_MAX = 10

// What the first read after connecting found: the only proof the key works,
// because saving it checks nothing (ai-trainer-ops#88).
type KeyCheck = { activities: number } | 'failed' | null

function keyCheckText(check: Exclude<KeyCheck, null | 'failed'>) {
  if (check.activities === 0) {
    return 'The key works, but there are no activities from the last month yet.'
  }
  const count =
    check.activities >= ACTIVITY_SAMPLE_MAX ? `${ACTIVITY_SAMPLE_MAX} or more` : check.activities
  return `The key works: ${count} activities from the last month found.`
}

export default function IntervalsConnect() {
  const authToken = useAppStore((s) => s.authToken)
  const intervalsConnection = useAppStore((s) => s.intervalsConnection)
  const setIntervalsConnection = useAppStore((s) => s.setIntervalsConnection)
  const setIntervalsAnalysisComplete = useAppStore((s) => s.setIntervalsAnalysisComplete)

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
      setApiKey('')
      setAthleteId(connection.athleteId)
      setAthleteName(connection.athleteName ?? '')
      try {
        const recent = await getIntervalsActivities(authToken)
        setKeyCheck({ activities: recent.length })
      } catch {
        setKeyCheck('failed')
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      console.error('[IntervalsConnect] Failed to save Intervals.icu credentials:', message, err)
      setError('Could not save the Intervals.icu connection. Please check the credentials and try again.')
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
      <div className="flex items-center gap-3 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
        <CheckCircle size={18} className="text-green-600" />
        <div className="flex-1">
          <p className="text-sm font-semibold text-green-800">Connected to Intervals.icu</p>
          <p className="text-xs text-green-600">
            {intervalsConnection.athleteName || `Athlete ${intervalsConnection.athleteId}`}
          </p>
          {keyCheck && keyCheck !== 'failed' && (
            <p className="text-xs text-green-700 mt-1">{keyCheckText(keyCheck)}</p>
          )}
          {keyCheck === 'failed' && (
            <p role="alert" className="text-xs text-red-700 mt-1">
              intervals.icu did not accept this key. Disconnect, copy the whole API key again and
              reconnect.
            </p>
          )}
        </div>
        <button
          onClick={() => void handleDisconnect()}
          disabled={isSaving}
          className="flex items-center gap-1 text-xs text-red-500 hover:text-red-700 disabled:opacity-50"
        >
          <Link2Off size={14} />
          Disconnect
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-3 border border-gray-200 rounded-xl p-4">
      <div>
        <p className="text-sm font-semibold text-gray-900">Connect Intervals.icu</p>
        <ol className="mt-2 list-decimal pl-4 space-y-1 text-xs text-gray-600">
          <li>
            Open{' '}
            <a
              href={INTERVALS_SETTINGS_URL}
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-amber-600 hover:text-amber-700 underline"
            >
              your intervals.icu settings
            </a>{' '}
            and scroll down to <strong>Developer Settings</strong>.
          </li>
          <li>
            Copy the <strong>API Key</strong>. If the field is empty, generate one there.
          </li>
          <li>Paste it below and connect. That is all: the key knows whose account it is.</li>
        </ol>
      </div>
      <label className="block text-xs font-semibold text-gray-700">
        API Key
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
          autoComplete="off"
        />
      </label>
      {/* Only for a key that reaches more than one athlete, such as a coach's. */}
      <details className="text-xs text-gray-600">
        <summary className="cursor-pointer select-none">Athlete ID and name (optional)</summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <label className="block text-xs font-semibold text-gray-700">
            Athlete ID
            <input
              type="text"
              value={athleteId}
              onChange={(e) => setAthleteId(e.target.value)}
              className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="Leave blank for your own account"
            />
          </label>
          <label className="block text-xs font-semibold text-gray-700">
            Athlete Name
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
        className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
      >
        <Save size={15} />
        {isSaving ? 'Saving...' : 'Connect Intervals.icu'}
      </button>
      {error && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
          <AlertCircle size={14} className="shrink-0 mt-0.5" />
          {error}
        </div>
      )}
    </div>
  )
}
