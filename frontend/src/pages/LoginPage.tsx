import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import AuthShell from '../components/AuthShell'
import { loginStep, loginWithTotp } from '../services/auth'
import { useAppStore } from '../store/useAppStore'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  // Set once the password is accepted and a code is still needed (#688). Its
  // presence is what swaps the form over, so the password step is never shown
  // again with a live challenge in hand.
  const [challenge, setChallenge] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [rememberDevice, setRememberDevice] = useState(false)
  // Survives the trip back to the password step, so the reason a code was
  // rejected is still on screen once the challenge is gone.
  const [mfaError, setMfaError] = useState<string | null>(null)

  const finishSignIn = async (token: string) => {
    setAuthToken(token)
    await loadUserData(token)
    navigate('/')
  }

  const loginMutation = useMutation({
    mutationFn: async () => {
      setMfaError(null)
      const result = await loginStep(email, password)
      if (result.kind === 'mfa') {
        setChallenge(result.challenge)
        return
      }
      await finishSignIn(result.token)
    },
  })

  const totpMutation = useMutation({
    mutationFn: async () => {
      if (!challenge) return
      await finishSignIn(await loginWithTotp(challenge, code, rememberDevice))
    },
    onError: (error: Error) => {
      /*
       * A challenge is single-use on the server, so a rejected code has spent
       * it — the code field it came from can no longer succeed, and retrying
       * there answers "this challenge was already used", which explains
       * nothing. So this goes back to the password step and carries the reason
       * with it rather than leaving a dead end on screen.
       *
       * Consuming the challenge on failure is the stricter choice and is kept
       * deliberately: it means one guess per password entry, on top of the
       * per-account rate limit.
       */
      setMfaError(
        error instanceof Error
          ? `${error.message} Please sign in again.`
          : 'That code did not work. Please sign in again.',
      )
      setChallenge(null)
      setCode('')
    },
  })

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    loginMutation.mutate()
  }

  const handleTotpSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    totpMutation.mutate()
  }

  const startOver = () => {
    setChallenge(null)
    setCode('')
    totpMutation.reset()
    loginMutation.reset()
  }

  return (
    <AuthShell
      eyebrow="Welcome back"
      title="Your coach kept up while you were out."
      subtitle="Rides synced, plan adjusted. Pick up the conversation wherever you left it."
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">
          {challenge ? 'Two-Factor Code' : 'Sign In'}
        </h2>
        <p className="text-sm text-gray-500 mb-5">
          {challenge
            ? 'Open your authenticator app and enter the current 6-digit code.'
            : "Back to your coach, your plan and tomorrow's session."}
        </p>

        {challenge ? (
          <form onSubmit={handleTotpSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Code</label>
              <input
                type="text"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg font-mono tracking-widest text-center focus:ring-amber-500 focus:border-amber-500"
                placeholder="000000"
                /* Not type="number": a code has leading zeros and is not a
                   quantity, and the spinner arrows are nonsense here. */
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
              />
              <p className="text-xs text-gray-500 mt-1">
                Lost your phone? Enter one of your recovery codes instead.
              </p>
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={rememberDevice}
                onChange={(event) => setRememberDevice(event.target.checked)}
                className="rounded border-gray-300 text-amber-500 focus:ring-amber-500"
              />
              Trust this device for 30 days
            </label>

            {/*
              No error box on this step: a rejected code clears the challenge,
              which unmounts this branch, and the reason is shown on the
              password step instead. Anything rendered here would be dead.
            */}

            <button
              type="submit"
              disabled={totpMutation.isPending || !code.trim()}
              className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {totpMutation.isPending ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  Verifying...
                </>
              ) : (
                'Verify'
              )}
            </button>

            <button
              type="button"
              onClick={startOver}
              className="w-full text-sm text-gray-500 hover:text-gray-700"
            >
              Back to sign in
            </button>
          </form>
        ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="you@example.com"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="Your password"
              required
            />
          </div>

          {(loginMutation.error || mfaError) && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {loginMutation.error
                ? loginMutation.error instanceof Error
                  ? loginMutation.error.message
                  : 'Login failed'
                : mfaError}
            </div>
          )}

          <button
            type="submit"
            disabled={loginMutation.isPending}
            className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {loginMutation.isPending ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                Signing in...
              </>
            ) : (
              'Sign In'
            )}
          </button>
        </form>
        )}

        <p className="text-sm text-gray-500 mt-5 text-center">
          Need an account?{' '}
          <Link to="/register" className="text-amber-600 hover:text-amber-700 font-semibold">
            Register
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
