import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { LogOut, ShieldAlert } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { revokeAllSessions } from '../services/sessions'

/**
 * Signing out everywhere (#704).
 *
 * Kept apart from the two-factor card even though both ask for the password:
 * that one is about how you prove who you are next time, this one is about
 * ending access that has already been granted. Someone looking for "my token
 * leaked, make it stop" should not have to read a 2FA panel to find it.
 *
 * The success path logs this browser out too, because the request really does
 * invalidate the token it was made with. Doing anything else — staying on the
 * page, re-issuing quietly — would misrepresent what just happened.
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
      logout()
    },
  })

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
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
            <ShieldAlert size={14} className="flex-shrink-0 mt-0.5 text-amber-500" />
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
