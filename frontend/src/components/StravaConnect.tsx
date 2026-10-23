import { useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { useT } from '../i18n'
import { disconnectStrava, getStravaAuthUrl } from '../services/strava'
import { AlertCircle, Link2Off } from 'lucide-react'

// Without the server's Strava credentials the button can only fail, and its
// failure used to read "please try again" (ai-trainer-ops#131).
const NOT_SET_UP = 'Strava cannot be connected directly on this server. Connect Strava through intervals.icu instead.'

interface StravaConnectProps {
  onBeforeConnect?: () => Promise<void> | void
}

export default function StravaConnect({ onBeforeConnect }: StravaConnectProps) {
  const authToken = useAppStore((s) => s.authToken)
  const stravaConnection = useAppStore((s) => s.stravaConnection)
  const setStravaConnection = useAppStore((s) => s.setStravaConnection)
  const setRiderAssessment = useAppStore((s) => s.setRiderAssessment)
  const setStravaAnalysisComplete = useAppStore((s) => s.setStravaAnalysisComplete)
  const stravaAvailable = useAppStore((s) => s.stravaAvailable)

  const [connectError, setConnectError] = useState<string | null>(null)
  const t = useT()

  const handleConnect = async () => {
    if (!authToken) return
    setConnectError(null)
    try {
      if (onBeforeConnect) {
        await onBeforeConnect()
      }
      const authUrl = await getStravaAuthUrl(authToken)
      window.location.href = authUrl
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      console.error('[StravaConnect] Failed to get Strava auth URL:', message, err)
      // A 503 that says the credentials are missing: trying again cannot help.
      // A gateway's 503 (a deploy restarting the containers) is the same status
      // and passes, so the server's own words decide (review of #948).
      const status = (err as { status?: number } | null)?.status
      setConnectError(
        status === 503 && /not configured/i.test(message)
          ? NOT_SET_UP
          : 'Could not start the Strava connection. Please try again.'
      )
    }
  }

  const handleDisconnect = async () => {
    if (!authToken) return
    await disconnectStrava(authToken)
    setStravaConnection(null)
    setRiderAssessment(null)
    setStravaAnalysisComplete(false)
  }

  if (stravaConnection) {
    return (
      // The same row as intervals.icu's (owner's review, 2026-10-23).
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <p className="text-sm text-gray-900">{t('Connected to Strava')}</p>
          {stravaConnection.athleteName && <p className="text-xs text-gray-500">{stravaConnection.athleteName}</p>}
        </div>
        <button
          onClick={handleDisconnect}
          className="flex shrink-0 items-center gap-1 text-xs text-gray-500 hover:text-red-600"
        >
          <Link2Off size={14} aria-hidden="true" />
          {t('Disconnect')}
        </button>
      </div>
    )
  }

  if (!stravaAvailable) {
    return <p className="text-xs text-gray-500">{t(NOT_SET_UP)}</p>
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={handleConnect}
        className="flex items-center gap-2 bg-[#fc4c02] text-white rounded-xl px-5 py-3 font-semibold text-sm hover:bg-[#e03d00] transition-colors shadow-xs"
      >
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
          <path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169" />
        </svg>
        {t('Connect Strava')}
      </button>
      {connectError && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
          <AlertCircle size={14} className="shrink-0 mt-0.5" />
          {t(connectError)}
        </div>
      )}
    </div>
  )
}
