import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import AuthShell from '../components/AuthShell'
import { requestPasswordReset } from '../services/auth'
import { useT } from '../i18n'

/** Ask for a reset link by email (ai-trainer-ops#35, #44.4).
 *
 * The answer reads the same whether the address has an account, because the
 * server answers the same: the page must not tell anyone who is registered.
 * A server without a mail provider answers 503, and the page then says what
 * the sign-in page said before mail existed.
 */
export default function ForgotPasswordPage() {
  const t = useT()
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState('')
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSending(true)
    setError('')
    try {
      setSent(await requestPasswordReset(email))
    } catch (err) {
      if ((err as { status?: number }).status === 503) setUnavailable(true)
      else setError(err instanceof Error ? err.message : t('The link could not be sent. Please try again.'))
    } finally {
      setSending(false)
    }
  }

  return (
    <AuthShell
      eyebrow={t('Forgot your password?')}
      title={t('Back on the bike in a minute.')}
      subtitle={t('We mail a link to the address you signed up with. It sets a new password and works once.')}
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">{t('Reset your password')}</h2>
        {unavailable ? (
          <p className="text-sm text-gray-600 mt-3">
            {t('Resetting it by email is not possible on this server. Ask the person who invited you; they can reset it for you.')}
          </p>
        ) : sent ? (
          <p role="status" className="text-sm text-gray-700 mt-3">
            {sent} {t('Check your spam folder if it does not arrive.')}
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 mt-4">
            <div>
              <label htmlFor="reset-email" className="block text-sm font-medium text-gray-700 mb-1">
                {t('Email')}
              </label>
              <input
                id="reset-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                placeholder="you@example.com"
                required
              />
            </div>
            {error && (
              <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
                {error}
              </div>
            )}
            <button
              type="submit"
              disabled={sending}
              className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {sending && <Loader2 size={18} className="animate-spin" aria-hidden="true" />}
              {t('Send the link')}
            </button>
          </form>
        )}
        <p className="text-sm text-gray-500 mt-5 text-center">
          <Link to="/login" className="text-amber-600 hover:text-amber-700 font-semibold">
            {t('Back to sign in')}
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
