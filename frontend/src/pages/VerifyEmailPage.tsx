import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import AuthShell from '../components/AuthShell'
import { resendEmailVerification, verifyEmail } from '../services/auth'
import { useT } from '../i18n'

type State = 'checking' | 'done' | 'failed'

/** Confirm a new account's address from the link in its mail (app #687).
 *
 * The token arrives in the URL fragment, which no server sees, and is taken out
 * of the address bar once read, like the password reset link.
 */
export default function VerifyEmailPage() {
  const t = useT()
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '')
  const [state, setState] = useState<State>(token ? 'checking' : 'failed')
  const [error, setError] = useState(token ? '' : t('This link is incomplete. Open it from the mail again.'))
  const [email, setEmail] = useState('')
  const [resent, setResent] = useState('')
  const sent = useRef(false)

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname)
    // Once: StrictMode runs effects twice, and the second answer would be about
    // a link the first one had already used.
    if (!token || sent.current) return
    sent.current = true
    verifyEmail(token)
      .then(() => setState('done'))
      .catch((err) => {
        setError(err instanceof Error ? err.message : t('This link is no longer valid.'))
        setState('failed')
      })
  }, [token])

  const resend = async (event: FormEvent) => {
    event.preventDefault()
    try {
      setResent(await resendEmailVerification(email))
    } catch (err) {
      setResent(err instanceof Error ? err.message : t('Please try again in a minute.'))
    }
  }

  // Once the link worked the page says so, rather than still asking for it
  // (ai-trainer-ops#176). Checking and a failed link keep the request.
  const confirmed = state === 'done'
  return (
    <AuthShell
      eyebrow={confirmed ? t('Address confirmed') : t('Confirm your address')}
      title={t('One click, then your coach.')}
      subtitle={t('Confirming the address keeps the account yours and lets you reset its password.')}
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">
          {confirmed ? t('Email confirmed') : t('Confirm your email')}
        </h2>
        {state === 'checking' && (
          <p className="text-sm text-gray-600 mt-3 flex items-center gap-2">
            <Loader2 size={16} className="animate-spin" aria-hidden="true" /> {t('Checking your link…')}
          </p>
        )}
        {state === 'done' && (
          <p role="status" className="text-sm text-gray-700 mt-3">
            {t('Your address is confirmed.')}{' '}
            <Link to="/login" state={{ newAccount: true }} className="text-amber-600 hover:text-amber-700 font-semibold">
              {t('Sign in')}
            </Link>{' '}
            {t('to meet your coach.')}
          </p>
        )}
        {state === 'failed' && (
          <div className="mt-3 space-y-4">
            <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {t(error)}
            </div>
            {resent ? (
              <p role="status" className="text-sm text-gray-700">{t(resent)}</p>
            ) : (
              <form onSubmit={resend} className="space-y-3">
                <label htmlFor="verify-email-address" className="block text-sm font-medium text-gray-700">
                  {t('Send a new link to')}
                </label>
                <input
                  id="verify-email-address"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  autoComplete="email"
                  required
                />
                <button
                  type="submit"
                  className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold hover:bg-amber-400 transition-colors"
                >
                  {t('Send a new link')}
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </AuthShell>
  )
}
