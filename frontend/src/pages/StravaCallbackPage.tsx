import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { apiFetch } from '../services/api'
import { useImportProgress } from '../hooks/useImportProgress'

type Step = 'connecting' | 'importing' | 'done' | 'error'

export default function StravaCallbackPage() {
  const navigate = useNavigate()
  const authToken = useAppStore((s) => s.authToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [step, setStep] = useState<Step>('connecting')
  const [errorMsg, setErrorMsg] = useState('')
  const redirectScheduled = useRef(false)

  const progress = useImportProgress()

  // Kick off the OAuth finalisation once
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const error = params.get('error')
    const success = params.get('success')

    if (error) {
      setErrorMsg(decodeURIComponent(error))
      setStep('error')
      return
    }
    if (!success) {
      setErrorMsg('No success confirmation received. Please try again.')
      setStep('error')
      return
    }

    const finalise = async () => {
      if (authToken) {
        await loadUserData(authToken).catch(() => {})
      }
      setStep('importing')
      // Fire-and-forget — backend responds 202 immediately
      apiFetch('/strava/import-history', { method: 'POST', token: authToken }).catch(() => {})
    }
    void finalise()
  }, [authToken, loadUserData]) // eslint-disable-line react-hooks/exhaustive-deps

  // Watch polling state to detect completion
  useEffect(() => {
    if (step !== 'importing') return
    if (progress.status !== 'done' && progress.status !== 'error') return
    if (redirectScheduled.current) return

    redirectScheduled.current = true
    setStep('done')
    setTimeout(() => navigate('/'), 2000)
  }, [step, progress.status, navigate])

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl p-8 max-w-sm w-full text-center">
        {step === 'connecting' && (
          <>
            <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">Connecting to Strava...</h2>
            <p className="text-sm text-gray-500 mt-1">Finalising the connection</p>
          </>
        )}
        {step === 'importing' && (() => {
          const pct = progress.total > 0
            ? Math.round((progress.processed / progress.total) * 100)
            : null
          return (
            <>
              <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
              <h2 className="text-lg font-bold text-gray-900">Importing ride history…</h2>
              {pct !== null ? (
                <>
                  <div className="mt-4 w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-amber-500 h-2 rounded-full transition-all duration-300"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="text-sm text-gray-500 mt-2">
                    {progress.processed} / {progress.total} rides ({pct}%)
                  </p>
                </>
              ) : (
                <p className="text-sm text-gray-500 mt-1">Fetching your ride list…</p>
              )}
            </>
          )
        })()}
        {step === 'done' && (
          <>
            <CheckCircle size={40} className="text-green-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">All set!</h2>
            <p className="text-sm text-gray-500 mt-1">
              {progress.processed > 0
                ? `${progress.processed} ride${progress.processed !== 1 ? 's' : ''} imported`
                : 'Ride history imported'}
            </p>
            <p className="text-xs text-gray-400 mt-1">Redirecting to dashboard…</p>
          </>
        )}
        {step === 'error' && (
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
