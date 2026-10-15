import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Eye, Loader2, XCircle } from 'lucide-react'
import AuthShell from '../components/AuthShell'
import { confirmPasswordReset } from '../services/auth'
import { getPasswordChecks, revealWhileHeld } from '../utils/passwordChecks'

/** Set a new password from the link in the reset mail (ai-trainer-ops#35).
 *
 * The token arrives in the URL fragment, which no server sees. It is read once
 * and taken out of the address bar, so it does not stay in the history.
 */
export default function ResetPasswordPage() {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isVisible, setIsVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname)
  }, [])

  // The reset page knows neither name nor email; the server checks those.
  const checks = getPasswordChecks(password, '', '')
  const matches = confirmPassword.length > 0 && password === confirmPassword
  const mismatch = confirmPassword.length > 0 && password !== confirmPassword
  const canSubmit = checks.every((check) => check.valid) && matches && !saving

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await confirmPasswordReset(token, password)
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The password could not be changed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <AuthShell
      eyebrow="New password"
      title="Back on the bike in a minute."
      subtitle="Choose a new password. Every device signed in to your account is signed out."
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">Set a new password</h2>
        {!token ? (
          <p className="text-sm text-gray-600 mt-3">
            This link is incomplete. Open it from the mail again, or{' '}
            <Link to="/forgot-password" className="text-amber-600 hover:text-amber-700 font-semibold">
              ask for a new one
            </Link>
            .
          </p>
        ) : done ? (
          <p role="status" className="text-sm text-gray-700 mt-3">
            Your password is changed, and every session is signed out.{' '}
            <Link to="/login" className="text-amber-600 hover:text-amber-700 font-semibold">
              Sign in
            </Link>{' '}
            with the new one.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 mt-4">
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-gray-700 mb-1">
                New password
              </label>
              <div className="relative">
                <input
                  id="new-password"
                  type={isVisible ? 'text' : 'password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full border border-gray-300 rounded-lg pl-3 pr-11 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  autoComplete="new-password"
                  minLength={10}
                  required
                />
                <button
                  type="button"
                  aria-label="Hold to show password"
                  aria-pressed={isVisible}
                  className="absolute inset-y-0 right-0 w-10 flex items-center justify-center text-gray-500 hover:text-gray-800"
                  {...revealWhileHeld(setIsVisible)}
                >
                  <Eye size={18} aria-hidden="true" />
                </button>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-1.5 text-xs">
                {checks.map((check) => (
                  <div
                    key={check.id}
                    className={check.valid ? 'flex items-center gap-2 text-green-700' : 'flex items-center gap-2 text-gray-500'}
                  >
                    {check.valid ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
                    <span>{check.label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <label htmlFor="confirm-new-password" className="block text-sm font-medium text-gray-700 mb-1">
                Confirm new password
              </label>
              <input
                id="confirm-new-password"
                type={isVisible ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className={`w-full border rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 ${
                  mismatch ? 'border-red-300 bg-red-50' : 'border-gray-300'
                }`}
                autoComplete="new-password"
                required
                aria-invalid={mismatch}
              />
              {mismatch && <p className="text-xs text-red-600 mt-1">The two passwords do not match.</p>}
            </div>
            {error && (
              <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
                {error}{' '}
                <Link to="/forgot-password" className="font-semibold underline">
                  Ask for a new link
                </Link>
              </div>
            )}
            <button
              type="submit"
              disabled={!canSubmit}
              className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {saving && <Loader2 size={18} className="animate-spin" aria-hidden="true" />}
              Set the new password
            </button>
          </form>
        )}
      </div>
    </AuthShell>
  )
}
