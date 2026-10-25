import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { CheckCircle2, XCircle } from 'lucide-react'
import { button } from './ui'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { useAuthToken } from '../hooks/useAuthToken'
import { changePassword } from '../services/auth'
import { signOut } from '../services/sessions'
import { getPasswordChecks, serverPasswordMessage } from '../utils/passwordChecks'
import { useT } from '../i18n'

/**
 * A new password while signed in (ai-trainer-ops#172).
 *
 * Until this card, the only way was the reset by mail: sign out, wait for the
 * link, and with the mailbox gone, no way at all. The server ends every
 * session on success, this one included, so the card says so and signs this
 * browser out from the same button as "Sign out everywhere" does.
 */
export default function PasswordSettings() {
  const authToken = useAuthToken()
  const { logout, userProfile } = useAppStore(
    useShallow((s) => ({ logout: s.logout, userProfile: s.userProfile }))
  )
  const t = useT()
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')

  const checks = getPasswordChecks(next, userProfile?.name ?? '', userProfile?.email ?? '')
  const mismatch = repeat.length > 0 && next !== repeat
  const canSubmit =
    Boolean(current) && checks.every((check) => check.valid) && repeat.length > 0 && !mismatch

  const change = useMutation({
    mutationFn: () => changePassword(authToken, current, next),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      setRepeat('')
    },
  })

  const close = () => {
    setOpen(false)
    setCurrent('')
    setNext('')
    setRepeat('')
    change.reset()
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (canSubmit && !change.isPending) change.mutate()
  }

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Password')}</h3>

      {change.isSuccess ? (
        <div role="status" className="space-y-3">
          <p className="text-sm text-gray-700">
            {t('Your password is changed, and every session is signed out.')}
          </p>
          <button
            onClick={() => signOut(logout)}
            className={button}
          >
            {t('Sign in with the new password')}
          </button>
        </div>
      ) : (
        <>
          <p className="text-sm text-gray-500 mb-4">
            {t('A new password signs you out of every browser and device, including this one.')}
          </p>

          {!open ? (
            <button
              onClick={() => setOpen(true)}
              className={button}
            >
              {t('Change password')}
            </button>
          ) : (
            <form onSubmit={submit} className="border border-gray-200 rounded-lg p-3 space-y-3">
              <div>
                <label htmlFor="password-current" className="block text-sm font-medium text-gray-700 mb-1">
                  {t('Current password')}
                </label>
                <input
                  id="password-current"
                  type="password"
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  autoComplete="current-password"
                />
              </div>
              <div>
                <label htmlFor="password-new" className="block text-sm font-medium text-gray-700 mb-1">
                  {t('New password')}
                </label>
                <input
                  id="password-new"
                  type="password"
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  autoComplete="new-password"
                />
                <div className="mt-2 grid grid-cols-1 gap-1 text-xs">
                  {checks.map((check) => (
                    <div
                      key={check.id}
                      className={check.valid ? 'flex items-center gap-2 text-gray-900' : 'flex items-center gap-2 text-gray-400'}
                    >
                      {check.valid ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
                      <span>{t(check.label, check.vars)}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <label htmlFor="password-repeat" className="block text-sm font-medium text-gray-700 mb-1">
                  {t('Confirm new password')}
                </label>
                <input
                  id="password-repeat"
                  type="password"
                  value={repeat}
                  onChange={(e) => setRepeat(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  autoComplete="new-password"
                />
                {mismatch && <p className="mt-1 text-sm text-red-600">{t('The two new passwords differ.')}</p>}
              </div>
              {change.error && (
                <p role="alert" className="text-sm text-red-600">
                  {change.error instanceof Error
                    ? serverPasswordMessage(t, change.error.message)
                    : t('The password could not be changed.')}
                </p>
              )}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={!canSubmit || change.isPending}
                  className={button}
                >
                  {change.isPending ? t('Saving…') : t('Change password')}
                </button>
                <button type="button" onClick={close} className="text-sm text-gray-500 hover:text-gray-700 px-2">
                  {t('Cancel')}
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </div>
  )
}
