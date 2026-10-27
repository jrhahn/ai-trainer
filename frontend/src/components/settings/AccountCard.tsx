import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { button, cardTitle, fieldLabel, input, textButton } from '../ui'
import { useAppStore } from '../../store/useAppStore'
import { useAuthToken } from '../../hooks/useAuthToken'
import { useProfile } from '../../queries/profile'
import { signOut, revokeAllSessions } from '../../services/sessions'
import { useT } from '../../i18n'
import { DeleteAccount } from './AccountData'

/**
 * Who is signed in, both ways to sign out, and the way to delete the account,
 * in one card (ai-trainer-ops#310, #312).
 *
 * They were three cards. "Account" had "Sign Out". "Active Sessions" sat right
 * under it (#704), because the first person looking for "Sign out everywhere"
 * clicked "Sign Out" instead. The deletion was a "Danger Zone" under "Your
 * data", whose button read "Reset All Data"; the owner looked for it here.
 *
 * "Sign out everywhere" signs this browser out too, through `signOut`, so
 * Authelia's cookie goes with the app token. The request really does
 * invalidate the token that made it, and staying on the page would leave a
 * session the server has already refused.
 */
export default function AccountCard() {
  const t = useT()
  const authToken = useAuthToken()
  const userProfile = useProfile()
  const logout = useAppStore((s) => s.logout)
  const [confirming, setConfirming] = useState(false)
  const [password, setPassword] = useState('')

  const revokeMutation = useMutation({
    mutationFn: () => revokeAllSessions(authToken, password),
    onSuccess: () => {
      setPassword('')
      // The full sign-out, not just `logout()`. The server has ended every app
      // session; Authelia still holds its own cookie on the parent domain, and
      // leaving that behind would make "everywhere" untrue for the one session
      // the athlete is sitting in.
      signOut(logout)
    },
  })

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className={`${cardTitle} mb-1`}>{t('Account')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Signed in as {email}.', { email: userProfile?.email ?? t('unknown') })}
      </p>

      <div className="flex flex-wrap gap-2">
        <button onClick={() => signOut(logout)} className={button}>
          {t('Sign out')}
        </button>
        {!confirming && (
          <button onClick={() => setConfirming(true)} className={button}>
            {t('Sign out everywhere')}
          </button>
        )}
      </div>

      {!confirming && (
        <p className="mt-2 text-sm text-gray-500">
          {t('“Sign out everywhere” also ends this browser. Use it if someone else may have your session.')}
        </p>
      )}

      {confirming && (
        <div className="mt-3 border border-gray-200 rounded-lg p-3 space-y-2">
          <p className="text-sm text-gray-600">
            {t('You will be signed out here as well and will need to sign in again. Any device you told us to remember for two-factor will have to show a code next time.')}
          </p>
          <label htmlFor="session-revoke-password" className={fieldLabel}>
            {t('Confirm your password')}
          </label>
          <input
            id="session-revoke-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={input}
            autoComplete="current-password"
          />
          {revokeMutation.error && (
            <p className="text-sm text-red-600">
              {revokeMutation.error instanceof Error
                ? t(revokeMutation.error.message)
                : t('Could not sign out the other sessions')}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => revokeMutation.mutate()}
              disabled={revokeMutation.isPending || !password}
              className={button}
            >
              {revokeMutation.isPending ? t('Signing out…') : t('Sign out everywhere')}
            </button>
            <button
              onClick={() => {
                setConfirming(false)
                setPassword('')
              }}
              className={`${textButton} px-2`}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}

      <DeleteAccount />
    </div>
  )
}
