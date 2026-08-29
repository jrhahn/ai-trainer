import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle, Loader2, XCircle } from 'lucide-react'
import { getSessionToken } from '../services/auth'
import { useAppStore } from '../store/useAppStore'

export default function AuthCallbackPage() {
  const navigate = useNavigate()
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)
  const [error, setError] = useState<string | null>(null)
  const [complete, setComplete] = useState(false)
  const initialisedRef = useRef(false)

  useEffect(() => {
    if (initialisedRef.current) return
    initialisedRef.current = true

    void (async () => {
      try {
        const token = await getSessionToken()
        setAuthToken(token)
        await loadUserData(token)
        setComplete(true)
        navigate('/', { replace: true })
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not finish secure sign-in.')
      }
    })()
  }, [loadUserData, navigate, setAuthToken])

  return (
    <div className="min-h-screen bg-[#0f1116] flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl px-8 py-6 text-center w-full max-w-sm">
        {error ? (
          <>
            <XCircle size={40} className="text-red-500 mx-auto mb-4" />
            <h1 className="text-lg font-bold text-gray-900">Sign-in failed</h1>
            <p className="text-sm text-gray-500 mt-1">{error}</p>
            <button
              type="button"
              onClick={() => navigate('/login', { replace: true })}
              className="mt-5 w-full bg-amber-500 text-white rounded-xl px-6 py-2 text-sm font-semibold hover:bg-amber-600"
            >
              Back to sign in
            </button>
          </>
        ) : complete ? (
          <>
            <CheckCircle size={40} className="text-green-500 mx-auto mb-4" />
            <h1 className="text-lg font-bold text-gray-900">Signed in</h1>
          </>
        ) : (
          <>
            <Loader2 size={40} className="animate-spin text-amber-500 mx-auto mb-4" />
            <h1 className="text-lg font-bold text-gray-900">Finishing secure sign-in...</h1>
            <p className="text-sm text-gray-500 mt-1">Checking your verified Authelia session.</p>
          </>
        )}
      </div>
    </div>
  )
}
