import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { setProfile } from '../queries/profile'
import { triggerStravaHistoryImport } from '../services/strava'
import { fetchCurrentUser } from '../services/user'
import { useImportProgress } from '../hooks/useImportProgress'
import StravaImportSummary from '../components/StravaImportSummary'
import { useT } from '../i18n'

type Step = 'connecting' | 'importing' | 'done' | 'error'

function getInitialCallbackState(): { step: Step; errorMsg: string } {
  const params = new URLSearchParams(window.location.search)
  const error = params.get('error')
  const success = params.get('success')

  if (error) {
    return { step: 'error', errorMsg: decodeURIComponent(error) }
  }
  if (!success) {
    return {
      step: 'error',
      errorMsg: 'No success confirmation received. Please try again.',
    }
  }
  return { step: 'connecting', errorMsg: '' }
}

export default function StravaCallbackPage() {
  const navigate = useNavigate()
  // The messages are set in English and translated where they show.
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const setStravaConnection = useAppStore((s) => s.setStravaConnection)
  const setRiderAssessment = useAppStore((s) => s.setRiderAssessment)
  const setStravaAnalysisComplete = useAppStore((s) => s.setStravaAnalysisComplete)
  const setLastStravaActivityId = useAppStore((s) => s.setLastStravaActivityId)
  const setAiProvider = useAppStore((s) => s.setAiProvider)
  const setOnboarded = useAppStore((s) => s.setOnboarded)
  const initialState = getInitialCallbackState()

  const [step, setStep] = useState<Step>(initialState.step)
  const [errorMsg, setErrorMsg] = useState(initialState.errorMsg)
  const redirectScheduled = useRef(false)
  // Prevent the effect from re-running if the component re-renders after initialisation.
  const initialisedRef = useRef(false)

  const progress = useImportProgress({ poll: step === 'importing' })

  const refreshCurrentUser = async () => {
    if (!authToken) return
    const user = await fetchCurrentUser(authToken)
    setProfile(user.profile)
    setStravaConnection(user.stravaConnection)
    setRiderAssessment(user.riderAssessment)
    setStravaAnalysisComplete(user.stravaAnalysisComplete)
    setLastStravaActivityId(user.lastStravaActivityId)
    setAiProvider(user.aiProvider)
    setOnboarded(user.isOnboarded)
  }

  const startImport = async () => {
    if (!authToken) {
      setErrorMsg('Your session expired during the Strava redirect. Please sign in and try again.')
      setStep('error')
      return
    }
    setErrorMsg('')
    setStep('importing')
    try {
      await triggerStravaHistoryImport(authToken)
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Could not start the Strava import.')
      setStep('error')
    }
  }

  // Kick off the OAuth finalisation once, after the app's initial data load completes.
  // App.tsx calls loadUserData() on mount; waiting for isLoadingUserData to be false
  // ensures stravaConnection is up-to-date before we start the import, and avoids
  // triggering a second concurrent loadUserData call that would cause an infinite loop.
  useEffect(() => {
    if (isLoadingUserData) return
    if (initialisedRef.current) return
    initialisedRef.current = true

    const params = new URLSearchParams(window.location.search)
    const success = params.get('success')

    if (!success) {
      return
    }

    void (async () => {
      try {
        await refreshCurrentUser()
        await startImport()
      } catch (e) {
        setErrorMsg(e instanceof Error ? e.message : 'Could not refresh your Strava connection.')
        setStep('error')
      }
    })()
  }, [isLoadingUserData]) // eslint-disable-line react-hooks/exhaustive-deps

  // Watch polling state to detect completion
  useEffect(() => {
    if (step !== 'importing') return
    if (progress.status !== 'done' && progress.status !== 'error') return
    if (redirectScheduled.current) return

    if (progress.status === 'error') {
      queueMicrotask(() => {
        setErrorMsg(progress.error || 'The Strava import failed. Please try again.')
        setStep('error')
      })
      return
    }

    queueMicrotask(() => setStep('done'))
    if (progress.skipped === 0 && progress.failedActivities.length === 0) {
      redirectScheduled.current = true
      setTimeout(() => navigate('/'), 2000)
    }
  }, [step, progress.status, progress.error, progress.skipped, progress.failedActivities.length, navigate])

  const handleRetry = () => {
    redirectScheduled.current = false
    void startImport()
  }

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl p-8 max-w-sm w-full text-center">
        {step === 'connecting' && (
          <>
            <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">{t('Connecting to Strava...')}</h2>
            <p className="text-sm text-gray-500 mt-1">{t('Finalising the connection')}</p>
          </>
        )}
        {step === 'importing' && (() => {
          const pct = progress.total > 0
            ? Math.round((progress.processed / progress.total) * 100)
            : null
          return (
            <>
              <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
              <h2 className="text-lg font-bold text-gray-900">{t('Importing activity history…')}</h2>
              {pct !== null ? (
                <>
                  <div className="mt-4 w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-amber-500 h-2 rounded-full transition-all duration-300"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="text-sm text-gray-500 mt-2">
                    {t('Processed activities: {processed} / {total} ({pct}%)', {
                      processed: progress.processed,
                      total: progress.total,
                      pct,
                    })}
                  </p>
                </>
              ) : (
                <p className="text-sm text-gray-500 mt-1">{t('Fetching your activity list…')}</p>
              )}
            </>
          )
        })()}
        {step === 'done' && (
          <>
            <CheckCircle size={40} className="text-green-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">{t('Import complete')}</h2>
            <p className="text-sm text-gray-500 mt-1 mb-4">
              {t(progress.imported === 1 ? '{n} activity imported' : '{n} activities imported', { n: progress.imported })}
              {progress.skipped > 0 ? t(', {n} skipped', { n: progress.skipped }) : ''}
            </p>
            <StravaImportSummary progress={progress} />
            <button
              onClick={() => navigate('/')}
              className="mt-5 w-full bg-amber-500 text-gray-950 rounded-xl px-6 py-2 text-sm font-semibold hover:bg-amber-600"
            >
              {t('Continue to dashboard')}
            </button>
            {progress.skipped === 0 && progress.failedActivities.length === 0 && (
              <p className="text-xs text-gray-400 mt-2">{t('Redirecting to dashboard...')}</p>
            )}
          </>
        )}
        {step === 'error' && (
          <>
            <XCircle size={40} className="text-red-500 mx-auto mb-4" />
            <h2 className="text-lg font-bold text-gray-900">{t('Import Failed')}</h2>
            <p className="text-sm text-gray-500 mt-2">{t(errorMsg)}</p>
            <button
              onClick={handleRetry}
              className="mt-4 w-full bg-amber-500 text-gray-950 rounded-xl px-6 py-2 text-sm font-semibold hover:bg-amber-600"
            >
              {t('Retry import')}
            </button>
            <button
              onClick={() => navigate('/settings')}
              className="mt-2 w-full border border-gray-300 text-gray-700 rounded-xl px-6 py-2 text-sm font-semibold hover:bg-gray-50"
            >
              {t('Go to Settings')}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
