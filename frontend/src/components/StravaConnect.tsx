import { useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { disconnectStrava, getStravaAuthUrl } from '../services/strava'
import { AlertCircle, CheckCircle, Link2Off } from 'lucide-react'

export default function StravaConnect() {
  const authToken = useAppStore((s) => s.authToken)
  const stravaConnection = useAppStore((s) => s.stravaConnection)
  const setStravaConnection = useAppStore((s) => s.setStravaConnection)
  const setRiderAssessment = useAppStore((s) => s.setRiderAssessment)
  const setStravaAnalysisComplete = useAppStore((s) => s.setStravaAnalysisComplete)

  const [connectError, setConnectError] = useState<string | null>(null)

  const handleConnect = () => {
    if (!authToken) return
    setConnectError(null)
    void getStravaAuthUrl(authToken).then((authUrl) => {
      window.location.href = authUrl
    }).catch((err: unknown) => {
      const message = err instanceof Error ? err.message : 'Unknown error'
      console.error('[StravaConnect] Failed to get Strava auth URL:', message, err)
      setConnectError('Could not start the Strava connection. Please try again.')
    })
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
      <div className="flex items-center gap-3 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
        <CheckCircle size={18} className="text-green-600" />
        <div className="flex-1">
          <p className="text-sm font-semibold text-green-800">Connected to Strava</p>
          <p className="text-xs text-green-600">{stravaConnection.athleteName}</p>
        </div>
        <button
          onClick={handleDisconnect}
          className="flex items-center gap-1 text-xs text-red-500 hover:text-red-700"
        >
          <Link2Off size={14} />
          Disconnect
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={handleConnect}
        className="flex items-center gap-2 bg-[#fc4c02] text-white rounded-xl px-5 py-3 font-semibold text-sm hover:bg-[#e03d00] transition-colors shadow-sm"
      >
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
          <path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169" />
        </svg>
        Connect Strava
      </button>
      {connectError && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
          <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
          {connectError}
        </div>
      )}
    </div>
  )
}
