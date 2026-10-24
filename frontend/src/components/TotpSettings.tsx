import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { button, deleteButton, fieldLabel, input, textButton } from './ui'
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
import { useT } from '../i18n'

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
  const t = useT()

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
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Two-Factor Authentication')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Ask for a code from your authenticator app when signing in, so a stolen password is not enough on its own.')}
      </p>

      {recoveryCodes && (
        <div className="border border-gray-200 rounded-lg p-4 mb-4">
          <p className="text-sm font-semibold text-gray-900 mb-1">
            {t('Save these recovery codes now')}
          </p>
          <p className="text-sm text-gray-500 mb-3">
            {t('They are shown once and cannot be retrieved later. Each works a single time, and they are the only way back in if you lose your phone.')}
          </p>
          <div className="grid grid-cols-2 gap-1 font-mono text-sm text-gray-900 mb-3">
            {recoveryCodes.map((c) => (
              <span key={c}>{c}</span>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => void copyRecoveryCodes()}
              className={button}
            >
              {copied ? t('Copied') : t('Copy')}
            </button>
            <button
              onClick={() => setRecoveryCodes(null)}
              className={textButton}
            >
              {t('I have saved them')}
            </button>
          </div>
        </div>
      )}

      {status?.enabled && !recoveryCodes && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <span className="font-medium text-gray-900">{t('On')}</span>
            <span className="text-gray-500">
              {t(status.recoveryCodesRemaining === 1 ? '{n} recovery code left' : '{n} recovery codes left', {
                n: status.recoveryCodesRemaining,
              })}
            </span>
            {status.trustedDeviceCount > 0 && (
              <span className="text-gray-500">
                · {t(status.trustedDeviceCount === 1 ? '{n} trusted device' : '{n} trusted devices', { n: status.trustedDeviceCount })}
              </span>
            )}
          </div>

          {status.recoveryCodesRemaining === 0 && (
            <p className="text-sm text-gray-700">
              {t('No recovery codes left. If you lose your phone now, you will need server access to get back in — turn two-factor off and on again to get a fresh set.')}
            </p>
          )}

          <div className="flex gap-2 flex-wrap">
            {status.trustedDeviceCount > 0 && (
              <button
                onClick={() => revokeMutation.mutate()}
                disabled={revokeMutation.isPending}
                className={button}
              >
                {revokeMutation.isPending ? t('Revoking…') : t('Revoke trusted devices')}
              </button>
            )}
            <button
              onClick={() => setShowDisable((v) => !v)}
              className={deleteButton}
            >
              {t('Turn off')}
            </button>
          </div>

          {showDisable && (
            <div className="border border-gray-200 rounded-lg p-3 space-y-2">
              <label
                htmlFor="totp-disable-password"
                className={fieldLabel}
              >
                {t('Confirm your password to turn two-factor off')}
              </label>
              <input
                id="totp-disable-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={input}
                autoComplete="current-password"
              />
              {disableMutation.error && (
                <p className="text-sm text-red-600">
                  {disableMutation.error instanceof Error
                    ? t(disableMutation.error.message)
                    : t('Could not turn it off')}
                </p>
              )}
              <button
                onClick={() => disableMutation.mutate()}
                disabled={disableMutation.isPending || !password}
                className="inline-flex items-center rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
              >
                {disableMutation.isPending ? t('Turning off…') : t('Confirm')}
              </button>
            </div>
          )}
        </div>
      )}

      {!status?.enabled && !enrollment && !recoveryCodes && (
        <button
          onClick={() => enrollMutation.mutate()}
          disabled={enrollMutation.isPending}
          className={button}
        >
          {enrollMutation.isPending ? t('Setting up…') : t('Set up two-factor')}
        </button>
      )}

      {enrollment && (
        <div className="space-y-3">
          <p className="text-sm text-gray-700">
            {t('Scan this with your authenticator app, then enter the code it shows.')}
          </p>
          <div
            className="w-44 h-44 [&>svg]:w-full [&>svg]:h-full"
            dangerouslySetInnerHTML={{ __html: enrollment.qrSvg }}
          />
          <details className="text-sm text-gray-500">
            <summary className="cursor-pointer">{t("Can't scan it?")}</summary>
            <p className="mt-1">{t('Enter this key by hand:')}</p>
            <code className="font-mono text-gray-700 break-all">{enrollment.secret}</code>
          </details>

          <div>
            <label
              htmlFor="totp-enroll-code"
              className={fieldLabel}
            >
              {t('Code from the app')}
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
            <p className="text-sm text-red-600">
              {confirmMutation.error instanceof Error
                ? t(confirmMutation.error.message)
                : t('That code did not work')}
            </p>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => confirmMutation.mutate()}
              disabled={confirmMutation.isPending || !code.trim()}
              className={button}
            >
              {confirmMutation.isPending ? t('Checking…') : t('Turn on')}
            </button>
            <button
              onClick={() => {
                setEnrollment(null)
                setCode('')
              }}
              className={`${textButton} px-2`}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
