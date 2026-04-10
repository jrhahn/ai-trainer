import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'

export default function StravaCallbackPage() {
  const navigate = useNavigate()
  const setStravaTokens = useAppStore((s) => s.setStravaTokens)

  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const error = params.get('error')

    if (error) {
      setErrorMsg(decodeURIComponent(error))
      setStatus('error')
      return
    }

    const accessToken = params.get('access_token')
    const refreshToken = params.get('refresh_token')
    const expiresAt = params.get('expires_at')
    const athleteId = params.get('athlete_id')
    const athleteName = params.get('athlete_name')

    if (!accessToken || !refreshToken || !expiresAt || !athleteId) {
      setErrorMsg('Incomplete token data received. Please try again.')
      setStatus('error')
      return
    }

    setStravaTokens({
      accessToken,
      refreshToken,
      expiresAt: Number(expiresAt),
      athleteId: Number(athleteId),
      athleteName: athleteName ?? '',
    })
    setStatus('success')
    setTimeout(() => navigate('/'), 2000)
  }, [setStravaTokens, navigate])

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl p-8 max-w-sm w-full text-center">
        {status === 'loading' && (
          <>
            <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">Connecting to Strava...</h2>
            <p className="text-sm text-gray-500 mt-1">Exchanging authorization token</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle size={40} className="text-green-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">Connected!</h2>
            <p className="text-sm text-gray-500 mt-1">Redirecting to dashboard...</p>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle size={40} className="text-red-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">Connection Failed</h2>
            <p className="text-sm text-gray-500 mt-2">{errorMsg}</p>
            <button
              onClick={() => navigate('/settings')}
              className="mt-4 bg-amber-500 text-white rounded-xl px-6 py-2 text-sm font-semibold hover:bg-amber-600"
            >
              Go to Settings
            </button>
          </>
        )}
      </div>
    </div>
  )
}
