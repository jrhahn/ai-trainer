import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import AuthShell from '../components/AuthShell'
import { loginStep, loginWithTotp, resendEmailVerification } from '../services/auth'
import { useAppStore } from '../store/useAppStore'
import { useT } from '../i18n'

export default function LoginPage() {
  const t = useT()
  const navigate = useNavigate()
  // Set by the registration page when the server sent a confirmation link
  // first (app #687).
  const confirmEmail = (useLocation().state as { confirmEmail?: string } | null)?.confirmEmail
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [email, setEmail] = useState(confirmEmail ?? '')
  const [password, setPassword] = useState('')
  const [resent, setResent] = useState<string | null>(null)

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
          ? t('{reason} Please sign in again.', { reason: t(error.message) })
          : t('That code did not work. Please sign in again.'),
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
      eyebrow={t('Welcome back')}
      title={t('Your coach kept up while you were out.')}
      subtitle={t('Rides synced, plan adjusted. Pick up the conversation wherever you left it.')}
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">
          {challenge ? t('Two-Factor Code') : t('Sign In')}
        </h2>
        <p className="text-sm text-gray-500 mb-5">
          {challenge
            ? t('Open your authenticator app and enter the current 6-digit code.')
            : t("Back to your coach, your plan and tomorrow's session.")}
        </p>

        {challenge ? (
          <form onSubmit={handleTotpSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="login-totp-code"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Code
              </label>
              <input
                id="login-totp-code"
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
                {t('Lost your phone? Enter one of your recovery codes instead.')}
              </p>
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={rememberDevice}
                onChange={(event) => setRememberDevice(event.target.checked)}
                className="rounded border-gray-300 text-amber-500 focus:ring-amber-500"
              />
              {t('Trust this device for 30 days')}
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
                  {t('Verifying...')}
                </>
              ) : (
                t('Verify')
              )}
            </button>

            <button
              type="button"
              onClick={startOver}
              className="w-full text-sm text-gray-500 hover:text-gray-700"
            >
              {t('Back to sign in')}
            </button>
          </form>
        ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Email')}</label>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="you@example.com"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Password')}</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('Your password')}
              required
            />
          </div>

          <p className="text-right text-sm">
            <Link to="/forgot-password" className="text-gray-500 hover:text-gray-700">
              {t('Forgot your password?')}
            </Link>
          </p>

          {confirmEmail && !loginMutation.error && (
            <div role="status" className="bg-amber-50 border border-amber-200 text-amber-900 rounded-lg px-4 py-3 text-sm">
              {t('We sent a link to {email}. Open it to confirm your address, then sign in here.', { email: confirmEmail })}
            </div>
          )}

          {(loginMutation.error || mfaError) && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {loginMutation.error
                ? loginMutation.error instanceof Error
                  ? t(loginMutation.error.message)
                  : t('Login failed')
                : mfaError}
              {/* 403 is the one refusal of a right password: the address is not
                  confirmed yet (app #687). */}
              {(loginMutation.error as { status?: number } | null)?.status === 403 && (
                <div className="mt-2">
                  {resent ? (
                    <p role="status">{resent}</p>
                  ) : (
                    <button
                      type="button"
                      className="font-semibold underline"
                      onClick={async () => setResent(await resendEmailVerification(email).catch(() => t('Please try again in a minute.')))}
                    >
                      {t('Send the link again')}
                    </button>
                  )}
                </div>
              )}
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
                {t('Signing in...')}
              </>
            ) : (
              t('Sign In')
            )}
          </button>
        </form>
        )}

        <p className="text-sm text-gray-500 mt-5 text-center">
          {t('Need an account?')}{' '}
          <Link to="/register" className="text-amber-600 hover:text-amber-700 font-semibold">
            {t('Register')}
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
