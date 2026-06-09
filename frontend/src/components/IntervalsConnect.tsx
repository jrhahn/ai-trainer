import { useState } from 'react'
import { AlertCircle, CheckCircle, Link2Off, Save } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { disconnectIntervals, saveIntervalsConnection } from '../services/intervals'

export default function IntervalsConnect() {
  const authToken = useAppStore((s) => s.authToken)
  const intervalsConnection = useAppStore((s) => s.intervalsConnection)
  const setIntervalsConnection = useAppStore((s) => s.setIntervalsConnection)
  const setIntervalsAnalysisComplete = useAppStore((s) => s.setIntervalsAnalysisComplete)

  const [apiKey, setApiKey] = useState('')
  const [athleteId, setAthleteId] = useState(intervalsConnection?.athleteId ?? '0')
  const [athleteName, setAthleteName] = useState(intervalsConnection?.athleteName ?? '')
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        <p className="text-xs text-gray-500 mt-1">
          Use your Intervals.icu API key and athlete ID to sync activities from Intervals.icu.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
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
        <label className="block text-xs font-semibold text-gray-700">
          Athlete ID
          <input
            type="text"
            value={athleteId}
            onChange={(e) => setAthleteId(e.target.value)}
            className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            placeholder="0"
          />
        </label>
      </div>
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
          <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
          {error}
        </div>
      )}
    </div>
  )
}
