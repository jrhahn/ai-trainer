import { useState } from 'react'
import { useAppStore, type UserProfile } from '../../store/useAppStore'
import { useProfile } from '../../queries/profile'
import { useAuthToken } from '../../hooks/useAuthToken'
import { useSaveStatus } from '../../hooks/useSaveStatus'
import { dropDraftIfSent, leaveOnEnter, useNote, useProfileSave } from '../../hooks/useProfileSave'
import type { useMetricsPipeline } from '../../hooks/useMetricsPipeline'
import { estimateFTP } from '../../services/user'
import { slots, useT } from '../../i18n'
import SaveStatus from '../SaveStatus'
import { button, fieldLabel, input, note } from '../ui'

/** Settings → Profile: the heart rates, an age for the maximum, and the expert's
 *  FTP estimate to confirm (ai-trainer-ops#274: out of `SettingsPage`). */
export default function HeartRateFields({ pipeline }: { pipeline: ReturnType<typeof useMetricsPipeline> }) {
  const t = useT()
  const authToken = useAuthToken()
  const userProfile = useProfile()
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const { saveFields } = useProfileSave()
  const { updateMetrics, isPending: isPipelinePending } = pipeline

  const profileMaxHrInput = userProfile?.maxHeartRate != null ? String(userProfile.maxHeartRate) : ''
  const profileRestingHrInput = userProfile?.restingHeartRate != null ? String(userProfile.restingHeartRate) : ''

  // Heart Rate settings state
  const [maxHrDraft, setMaxHrDraft] = useState<string | null>(null)
  const [restingHrDraft, setRestingHrDraft] = useState<string | null>(null)
  const maxHrInput = maxHrDraft ?? profileMaxHrInput
  const restingHrInput = restingHrDraft ?? profileRestingHrInput
  const [ageInput, setAgeInput] = useState('')
  const [hrMsg, showHrMsg] = useNote()
  const hrStatus = useSaveStatus()
  // Two-step FTP confirmation after HR save
  const [ftpEstimate, setFtpEstimate] = useState<number | null>(null)
  const [ftpConfirmInput, setFtpConfirmInput] = useState('')

  // Step 1 of the expert flow: a saved heart rate gives an FTP estimate to
  // confirm. It feeds "Confirm & Recalculate", which rewrites history and is
  // expert-only (ai-trainer-ops#50); without Expert mode a save is a save.
  const estimateFtpFromHr = async (profile: UserProfile | undefined) => {
    if (!authToken || !profile || !isExpertMode) return
    setFtpEstimate(null)
    setFtpConfirmInput('')
    showHrMsg(null)
    try {
      const result = await estimateFTP(authToken, {
        maxHeartRate: profile.maxHeartRate,
        restingHeartRate: profile.restingHeartRate,
      })
      if (result.estimatedFTP !== null && result.estimatedFTP !== undefined) {
        setFtpEstimate(result.estimatedFTP)
        setFtpConfirmInput(String(result.estimatedFTP))
        showHrMsg({ type: 'success', text: t('Review your estimated FTP below and confirm to rebuild metrics.') })
      } else {
        showHrMsg({ type: 'success', text: t('No activity data available yet to estimate FTP — sync Strava first.') })
      }
    } catch {
      showHrMsg({ type: 'error', text: t('Could not estimate FTP from heart rate. Please try again.') })
    }
  }

  const commitHeartRate = (field: 'maxHeartRate' | 'restingHeartRate') => {
    const [raw, stored, setDraft] =
      field === 'maxHeartRate'
        ? [maxHrInput, profileMaxHrInput, setMaxHrDraft]
        : [restingHrInput, profileRestingHrInput, setRestingHrDraft]
    if (!raw.trim() || raw.trim() === stored) return setDraft(null)
    const parsed = parseInt(raw, 10)
    if (isNaN(parsed) || parsed <= 0) return hrStatus.fail('Please enter a heart rate in beats per minute.')
    void saveFields(hrStatus, { [field]: parsed }, dropDraftIfSent(setDraft, raw)).then(estimateFtpFromHr)
  }

  // Max HR estimated from age (min age 10 to avoid unrealistic values).
  const commitAge = () => {
    if (!ageInput.trim()) return
    const age = parseInt(ageInput, 10)
    if (isNaN(age) || age < 10) return hrStatus.fail('Please enter an age of at least 10.')
    const sent = ageInput
    void saveFields(hrStatus, { maxHeartRate: Math.max(100, 220 - age) }, () =>
      setAgeInput((current) => (current === sent ? '' : current))
    ).then(estimateFtpFromHr)
  }

  // Step 2: Confirm the FTP and trigger a full metrics recalculation.
  const handleConfirmFTP = async () => {
    if (!authToken) return
    const parsed = parseInt(ftpConfirmInput, 10)
    if (isNaN(parsed) || parsed <= 0) {
      showHrMsg({ type: 'error', text: t('Please enter a valid positive FTP value in watts.') })
      return
    }
    const confirmed = window.confirm(
      `${t('Recalculate TSS, ATL, CTL for stored activities?')}\n\n` +
        t('This will overwrite all historical training stress values using {reference} as the reference. This operation cannot be reversed.', {
          reference: `${parsed} W`,
        }) +
        `\n\n${t('Continue?')}`
    )
    if (!confirmed) return

    showHrMsg(null)
    try {
      const result = await updateMetrics({ currentFTP: parsed })
      setFtpEstimate(null)
      setFtpConfirmInput('')
      setMaxHrDraft(null)
      setRestingHrDraft(null)
      setAgeInput('')
      showHrMsg(
        {
          type: 'success',
          text: result.updated === 1
            ? t('Done! Recalculated 1 activity using FTP {ftp} W.', { ftp: result.ftpUsed })
            : t('Done! Recalculated {count} activities using FTP {ftp} W.', { count: result.updated, ftp: result.ftpUsed }),
        },
        6000,
      )
    } catch (e) {
      showHrMsg({ type: 'error', text: t(e instanceof Error ? e.message : 'Metrics recalculation failed. Please try again.') })
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Heart Rate Settings')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Max HR and resting HR are used for HR-based FTP estimation and training zones.')}
      </p>
      {(userProfile?.maxHeartRate != null || userProfile?.restingHeartRate != null) && (
        <p className="text-sm text-gray-500 mb-4">
          {t('Current:')}{' '}
          {userProfile.maxHeartRate != null && (
            <span className="font-medium text-gray-700 mr-2">{t('Max HR')}: <span className="text-gray-900">{userProfile.maxHeartRate} bpm</span></span>
          )}
          {userProfile.restingHeartRate != null && (
            <span className="font-medium text-gray-700 mr-2">{t('Resting HR')}: <span className="text-gray-900">{userProfile.restingHeartRate} bpm</span></span>
          )}
        </p>
      )}

      {hrMsg && <p className={`mb-4 ${note(hrMsg.type === 'success')}`}>{hrMsg.text}</p>}

      <div className="space-y-3 mb-4">
        <div>
          <label htmlFor="settings-max-hr" className={fieldLabel}>
            {t('Max Heart Rate (bpm)')}
          </label>
          <input
            type="number"
            min={1}
            id="settings-max-hr"
            value={maxHrInput}
            onChange={(e) => {
              setMaxHrDraft(e.target.value)
              hrStatus.clearError()
            }}
            onBlur={() => commitHeartRate('maxHeartRate')}
            onKeyDown={leaveOnEnter}
            placeholder={userProfile?.maxHeartRate != null ? String(userProfile.maxHeartRate) : t('e.g. {example}', { example: 185 })}
            className={input}
          />
          {!maxHrInput && (
            <div className="mt-2">
              <label className="block text-sm text-gray-500 mb-1">
                {t('Or enter your age to estimate Max HR (220 − age):')}
              </label>
              <input
                type="number"
                min={1}
                value={ageInput}
                onChange={(e) => {
                  setAgeInput(e.target.value)
                  hrStatus.clearError()
                }}
                onBlur={commitAge}
                onKeyDown={leaveOnEnter}
                placeholder={t('e.g. {example}', { example: 35 })}
                className={input}
              />
              {ageInput && (() => {
                const parsedAge = parseInt(ageInput, 10)
                const est = Number.isFinite(parsedAge) && parsedAge >= 10
                  ? Math.max(100, 220 - parsedAge)
                  : undefined
                return est !== undefined ? (
                  <p className="text-sm text-gray-500 mt-1">
                    {t('Estimated Max HR: {bpm} bpm', { bpm: est })}
                  </p>
                ) : null
              })()}
            </div>
          )}
        </div>
        <div>
          <label className={fieldLabel}>
            {t('Resting Heart Rate (bpm)')}
          </label>
          <input
            type="number"
            min={1}
            value={restingHrInput}
            onChange={(e) => {
              setRestingHrDraft(e.target.value)
              hrStatus.clearError()
            }}
            onBlur={() => commitHeartRate('restingHeartRate')}
            onKeyDown={leaveOnEnter}
            placeholder={userProfile?.restingHeartRate != null ? String(userProfile.restingHeartRate) : t('e.g. 55 (default: 60)')}
            className={input}
          />
        </div>
      </div>

      <SaveStatus state={hrStatus.state} />

      {/* Step 2: FTP confirmation panel */}
      {isExpertMode && ftpEstimate !== null && (
        <div className="mt-4 border border-gray-200 rounded-xl p-4 space-y-3">
          <p className="text-sm font-semibold text-gray-800">
            {t('Step 2 — Confirm FTP before rebuilding metrics')}
          </p>
          <p className="text-sm text-gray-600">
            {slots(
              t('Based on your activity history, your estimated FTP is {ftp}. You can adjust this value before recalculating all training-stress metrics (TSS, ATL, CTL, TSB).'),
              { ftp: <strong key="ftp" className="text-gray-900">{ftpEstimate} W</strong> },
            )}
          </p>
          <div className="flex gap-2">
            <div className="relative flex-1">
                                  <input
                type="number"
                min={1}
                value={ftpConfirmInput}
                onChange={(e) => setFtpConfirmInput(e.target.value)}
                className="w-full border border-gray-300 rounded-lg pl-3 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">W</span>
            </div>
            <button
              onClick={() => void handleConfirmFTP()}
              disabled={isPipelinePending || !ftpConfirmInput.trim()}
              className={button}
            >
              {isPipelinePending ? t('Recalculating…') : t('Confirm & Recalculate')}
            </button>
          </div>
          <p className="text-sm text-gray-700">
            {slots(t('This will overwrite all historical training-stress values and {strong}.'), {
              strong: <strong key="strong">{t('cannot be reversed')}</strong>,
            })}
          </p>
        </div>
      )}
    </div>

  )
}
