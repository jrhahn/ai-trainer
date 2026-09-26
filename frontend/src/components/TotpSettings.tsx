import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck, ShieldOff, Loader2, AlertCircle, Copy, Check } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import {
  confirmTotp,
  disableTotp,
  fetchTotpStatus,
  revokeTrustedDevices,
  startTotpEnrollment,
  type TotpEnrollment,
  type TotpStatus,
} from '../services/totp'

const TOTP_QUERY_KEY = 'totp-status'

/**
 * Setting up and managing the second factor (#688).
 *
 * The QR arrives as an SVG string from the backend rather than being generated
 * here — that keeps a QR library out of the bundle and needs no CSP exception
 * for it (#677). It is inserted with `dangerouslySetInnerHTML`, which is safe
 * for exactly one reason worth stating: the markup is produced by our own
 * server from a URI our own server built, and contains no user input at all.
 */
export default function TotpSettings() {
  const { authToken } = useAppStore(useShallow((s) => ({ authToken: s.authToken })))
  const queryClient = useQueryClient()

  const [enrollment, setEnrollment] = useState<TotpEnrollment | null>(null)
  const [code, setCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null)
  const [password, setPassword] = useState('')
  const [showDisable, setShowDisable] = useState(false)
  const [copied, setCopied] = useState(false)

  const { data: status } = useQuery<TotpStatus>({
    queryKey: [TOTP_QUERY_KEY],
    queryFn: () => fetchTotpStatus(authToken!),
    enabled: !!authToken,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: [TOTP_QUERY_KEY] })

  const enrollMutation = useMutation({
    mutationFn: () => startTotpEnrollment(authToken!),
    onSuccess: setEnrollment,
  })

  const confirmMutation = useMutation({
    mutationFn: () => confirmTotp(authToken!, code.trim()),
    onSuccess: (codes) => {
      // Shown once and never retrievable, so the enrollment panel stays open
      // until they are explicitly dismissed.
      setRecoveryCodes(codes)
      setEnrollment(null)
      setCode('')
      refresh()
    },
  })

  const disableMutation = useMutation({
    mutationFn: () => disableTotp(authToken!, password),
    onSuccess: () => {
      setPassword('')
      setShowDisable(false)
      refresh()
    },
  })

  const revokeMutation = useMutation({
    mutationFn: () => revokeTrustedDevices(authToken!),
    onSuccess: refresh,
  })

  const copyRecoveryCodes = async () => {
    if (!recoveryCodes) return
    try {
      await navigator.clipboard.writeText(recoveryCodes.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access can be refused; the codes are on screen regardless.
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
      <h2 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
        {status?.enabled ? (
          <ShieldCheck size={16} className="text-green-600" />
        ) : (
          <ShieldOff size={16} className="text-gray-400" />
        )}
        Two-Factor Authentication
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        Ask for a code from your authenticator app when signing in, so a stolen
        password is not enough on its own.
      </p>

      {recoveryCodes && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
          <p className="text-sm font-semibold text-amber-900 mb-1">
            Save these recovery codes now
          </p>
          <p className="text-xs text-amber-800 mb-3">
            They are shown once and cannot be retrieved later. Each works a single
            time, and they are the only way back in if you lose your phone.
          </p>
          <div className="grid grid-cols-2 gap-1 font-mono text-sm text-amber-900 mb-3">
            {recoveryCodes.map((c) => (
              <span key={c}>{c}</span>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => void copyRecoveryCodes()}
              className="flex items-center gap-1.5 border border-amber-300 text-amber-900 rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-amber-100"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              onClick={() => setRecoveryCodes(null)}
              className="text-xs text-amber-800 underline"
            >
              I have saved them
            </button>
          </div>
        </div>
      )}

      {status?.enabled && !recoveryCodes && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <span className="inline-flex items-center gap-1.5 font-semibold text-green-700 bg-green-50 border border-green-200 px-2.5 py-1 rounded-full">
              <ShieldCheck size={12} />
              On
            </span>
            <span className="text-gray-500">
              {status.recoveryCodesRemaining} recovery code
              {status.recoveryCodesRemaining === 1 ? '' : 's'} left
            </span>
            {status.trustedDeviceCount > 0 && (
              <span className="text-gray-500">
                · {status.trustedDeviceCount} trusted device
                {status.trustedDeviceCount === 1 ? '' : 's'}
              </span>
            )}
          </div>

          {status.recoveryCodesRemaining === 0 && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2 text-xs">
              <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
              No recovery codes left. If you lose your phone now, you will need
              server access to get back in — turn two-factor off and on again to
              get a fresh set.
            </div>
          )}

          <div className="flex gap-2 flex-wrap">
            {status.trustedDeviceCount > 0 && (
              <button
                onClick={() => revokeMutation.mutate()}
                disabled={revokeMutation.isPending}
                className="border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
              >
                {revokeMutation.isPending ? 'Revoking…' : 'Revoke trusted devices'}
              </button>
            )}
            <button
              onClick={() => setShowDisable((v) => !v)}
              className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100"
            >
              Turn off
            </button>
          </div>

          {showDisable && (
            <div className="border border-gray-200 rounded-lg p-3 space-y-2">
              <label
                htmlFor="totp-disable-password"
                className="block text-xs font-semibold text-gray-700"
              >
                Confirm your password to turn two-factor off
              </label>
              <input
                id="totp-disable-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                autoComplete="current-password"
              />
              {disableMutation.error && (
                <p className="text-xs text-red-600">
                  {disableMutation.error instanceof Error
                    ? disableMutation.error.message
                    : 'Could not turn it off'}
                </p>
              )}
              <button
                onClick={() => disableMutation.mutate()}
                disabled={disableMutation.isPending || !password}
                className="bg-red-600 text-white rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
              >
                {disableMutation.isPending ? 'Turning off…' : 'Confirm'}
              </button>
            </div>
          )}
        </div>
      )}

      {!status?.enabled && !enrollment && !recoveryCodes && (
        <button
          onClick={() => enrollMutation.mutate()}
          disabled={enrollMutation.isPending}
          className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
        >
          {enrollMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
          Set up two-factor
        </button>
      )}

      {enrollment && (
        <div className="space-y-3">
          <p className="text-sm text-gray-700">
            Scan this with your authenticator app, then enter the code it shows.
          </p>
          <div
            className="w-44 h-44 [&>svg]:w-full [&>svg]:h-full"
            dangerouslySetInnerHTML={{ __html: enrollment.qrSvg }}
          />
          <details className="text-xs text-gray-500">
            <summary className="cursor-pointer">Can't scan it?</summary>
            <p className="mt-1">Enter this key by hand:</p>
            <code className="font-mono text-gray-700 break-all">{enrollment.secret}</code>
          </details>

          <div>
            <label
              htmlFor="totp-enroll-code"
              className="block text-xs font-semibold text-gray-700 mb-1"
            >
              Code from the app
            </label>
            <input
              id="totp-enroll-code"
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-lg font-mono tracking-widest text-center"
              placeholder="000000"
              inputMode="numeric"
              autoComplete="one-time-code"
            />
          </div>

          {confirmMutation.error && (
            <p className="text-xs text-red-600">
              {confirmMutation.error instanceof Error
                ? confirmMutation.error.message
                : 'That code did not work'}
            </p>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => confirmMutation.mutate()}
              disabled={confirmMutation.isPending || !code.trim()}
              className="bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
            >
              {confirmMutation.isPending ? 'Checking…' : 'Turn on'}
            </button>
            <button
              onClick={() => {
                setEnrollment(null)
                setCode('')
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
