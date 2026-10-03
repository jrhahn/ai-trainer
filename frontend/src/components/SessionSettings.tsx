import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { LogOut, ShieldAlert } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { endSession, revokeAllSessions } from '../services/sessions'

/**
 * Signing out everywhere (#704).
 *
 * Sits directly under the Account card, next to the plain "Sign Out". It was
 * five cards further down at first, which read well as a taxonomy and failed
 * the only test that counts: the first person to go looking for it clicked
 * "Sign Out" instead. Someone who wants every session gone starts where
 * signing out is.
 *
 * The success path signs this browser out too — through `endSession`, so
 * Authelia's cookie goes with the app token. The request really does invalidate
 * the token that made it, and staying on the page would leave a session the
 * server has already refused.
 */
export default function SessionSettings() {
  const { authToken, logout } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, logout: s.logout }))
  )

  const [confirming, setConfirming] = useState(false)
  const [password, setPassword] = useState('')

  const revokeMutation = useMutation({
    mutationFn: () => revokeAllSessions(authToken!, password),
    onSuccess: () => {
      setPassword('')
      // The full sign-out, not just `logout()`. The server has ended every app
      // session; Authelia still holds its own cookie on the parent domain, and
      // leaving that behind would make "everywhere" untrue for the one session
      // the athlete is sitting in.
      endSession(logout)
    },
  })

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h2 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
        <LogOut size={16} className="text-gray-400" />
        Active Sessions
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        Sign out of every browser and device, including this one. Use this if you
        have been signed in somewhere you no longer control, or if you think
        someone else has a copy of your session.
      </p>

      {!confirming && (
        <button
          onClick={() => setConfirming(true)}
          className="border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50"
        >
          Sign out everywhere
        </button>
      )}

      {confirming && (
        <div className="border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-start gap-2 text-xs text-gray-600">
            <ShieldAlert size={14} className="shrink-0 mt-0.5 text-amber-500" />
            <span>
              You will be signed out here as well and will need to sign in again.
              Any device you told us to remember for two-factor will have to show a
              code next time.
            </span>
          </div>
          <label
            htmlFor="session-revoke-password"
            className="block text-xs font-semibold text-gray-700"
          >
            Confirm your password
          </label>
          <input
            id="session-revoke-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
            autoComplete="current-password"
          />
          {revokeMutation.error && (
            <p className="text-xs text-red-600">
              {revokeMutation.error instanceof Error
                ? revokeMutation.error.message
                : 'Could not sign out the other sessions'}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => revokeMutation.mutate()}
              disabled={revokeMutation.isPending || !password}
              className="bg-gray-900 text-white rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
            >
              {revokeMutation.isPending ? 'Signing out…' : 'Sign out everywhere'}
            </button>
            <button
              onClick={() => {
                setConfirming(false)
                setPassword('')
              }}
              className="text-sm text-gray-500 hover:text-gray-700 px-2"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
