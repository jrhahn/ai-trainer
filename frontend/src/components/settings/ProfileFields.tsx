import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../../store/useAppStore'
import { useProfile } from '../../queries/profile'
import { useAuthToken } from '../../hooks/useAuthToken'
import { useSaveStatus } from '../../hooks/useSaveStatus'
import { dropDraftIfSent, leaveOnEnter, useNote, useProfileSave } from '../../hooks/useProfileSave'
import type { useMetricsPipeline } from '../../hooks/useMetricsPipeline'
import { slots, useT } from '../../i18n'
import { formatPaceFromSeconds, parsePaceToSeconds } from '../../utils/pace'
import SaveStatus from '../SaveStatus'
import { button, input, note } from '../ui'

/** Settings → Profile: the name, the running pace, the FTP with its expert
 *  recalculation, and the body weight (ai-trainer-ops#274: out of `SettingsPage`).
 *  `pipeline` is the page's one, so the two recalculations cannot overlap. */
export default function ProfileFields({ pipeline }: { pipeline: ReturnType<typeof useMetricsPipeline> }) {
  const t = useT()
  const authToken = useAuthToken()
  const userProfile = useProfile()
  const { isExpertMode, ftpPlausibilityWarning } = useAppStore(
    useShallow((s) => ({
      isExpertMode: s.isExpertMode,
      ftpPlausibilityWarning: s.ftpPlausibilityWarning,
    })),
  )
  const { saveFields } = useProfileSave()
  const { updateMetrics, recalculateAll, isPending: isPipelinePending } = pipeline

  const profileNameInput = userProfile?.name ?? ''
  const profileFtpInput = userProfile?.currentFTP != null ? String(userProfile.currentFTP) : ''
  const profilePaceInput = formatPaceFromSeconds(userProfile?.thresholdPaceSecondsPerKm)
  const profileWeightInput = userProfile?.weightKg != null ? String(userProfile.weightKg) : ''

  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const nameInput = nameDraft ?? profileNameInput

  // FTP override state
  const [ftpDraft, setFtpDraft] = useState<string | null>(null)
  const ftpInput = ftpDraft ?? profileFtpInput
  const [ftpMsg, showFtpMsg] = useNote()

  // Running threshold-pace state (#716)
  const [paceDraft, setPaceDraft] = useState<string | null>(null)
  const paceInput = paceDraft ?? profilePaceInput

  // Body weight, optional (ai-trainer-ops#188)
  const [weightDraft, setWeightDraft] = useState<string | null>(null)
  const weightInput = weightDraft ?? profileWeightInput

  const nameStatus = useSaveStatus()
  const ftpStatus = useSaveStatus()
  const paceStatus = useSaveStatus()
  const weightStatus = useSaveStatus()

  // Sport-specific fields follow the sports the athlete chose (ai-trainer-ops#93).
  // With none recorded, every field shows, as before.
  const sports = userProfile?.sports ?? []
  const showsFtp = sports.length === 0 || sports.includes('cycling')
  const showsPace = sports.length === 0 || sports.includes('running')

  // An empty or unchanged field is no edit: it shows the stored value again.
  const commitName = () => {
    const trimmed = nameInput.trim()
    if (!trimmed || trimmed === profileNameInput) return setNameDraft(null)
    void saveFields(nameStatus, { name: trimmed }, dropDraftIfSent(setNameDraft, nameInput))
  }

  const commitFtp = () => {
    if (!ftpInput.trim() || ftpInput.trim() === profileFtpInput) return setFtpDraft(null)
    const parsed = parseInt(ftpInput, 10)
    if (isNaN(parsed) || parsed <= 0) return ftpStatus.fail('Please enter a valid positive FTP value in watts.')
    void saveFields(ftpStatus, { currentFTP: parsed }, dropDraftIfSent(setFtpDraft, ftpInput))
  }

  const commitPace = () => {
    if (!paceInput.trim() || paceInput.trim() === profilePaceInput) return setPaceDraft(null)
    const parsed = parsePaceToSeconds(paceInput)
    if (parsed === null) return paceStatus.fail('Enter a pace as minutes and seconds per km, e.g. 4:10 or 4.10.')
    void saveFields(paceStatus, { thresholdPaceSecondsPerKm: parsed }, dropDraftIfSent(setPaceDraft, paceInput))
  }

  // Unlike the fields above, an emptied weight is an edit: it is optional and
  // personal, so emptying the field removes it (ai-trainer-ops#188).
  const commitWeight = () => {
    const trimmed = weightInput.trim()
    if (trimmed === profileWeightInput) return setWeightDraft(null)
    if (!trimmed) return void saveFields(weightStatus, { weightKg: null }, dropDraftIfSent(setWeightDraft, weightInput))
    const parsed = Number(trimmed.replace(',', '.'))
    if (!(parsed >= 30 && parsed <= 250)) return weightStatus.fail('Enter your weight in kilograms, e.g. 70 or 68.5.')
    void saveFields(weightStatus, { weightKg: Math.round(parsed * 10) / 10 }, dropDraftIfSent(setWeightDraft, weightInput))
  }

  const handleRecalculate = async () => {
    if (!authToken) return
    // A value still in the field is used as typed. The click blurs the field
    // and starts its save, but that save may not have landed when the
    // recalculation reads the stored FTP.
    const overrideInput = ftpDraft !== null ? ftpInput.trim() : ''
    const parsed = overrideInput ? parseInt(overrideInput, 10) : undefined
    if (parsed !== undefined && (isNaN(parsed) || parsed <= 0)) {
      showFtpMsg({ type: 'error', text: t('Please enter a valid positive FTP value in watts.') })
      return
    }
    const confirmed = window.confirm(
      `${t('Recalculate TSS, ATL, CTL for stored activities?')}\n\n` +
        t('This will overwrite all historical training stress values using {reference} as the reference. This operation cannot be reversed.', {
          reference: parsed ? `${parsed} W` : t('your current FTP'),
        }) +
        `\n\n${t('Continue?')}`
    )
    if (!confirmed) return

    showFtpMsg(null)
    try {
      const result = parsed !== undefined
        ? await updateMetrics({ currentFTP: parsed })
        : await recalculateAll()
      if (parsed !== undefined) setFtpDraft(null)
      showFtpMsg(
        {
          type: 'success',
          text: result.updated === 1
            ? t('Recalculated 1 activity using FTP {ftp} W.', { ftp: result.ftpUsed })
            : t('Recalculated {count} activities using FTP {ftp} W.', { count: result.updated, ftp: result.ftpUsed }),
        },
        5000,
      )
    } catch (e) {
      showFtpMsg({ type: 'error', text: t(e instanceof Error ? e.message : 'Recalculation failed. Please try again.') })
    }
  }

  return (
    <>
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Name')}</h3>
      <div>
        <input
          type="text"
          id="settings-display-name"
          aria-label={t('Display Name')}
          value={nameInput}
          onChange={(e) => {
            setNameDraft(e.target.value)
            nameStatus.clearError()
          }}
          onBlur={commitName}
          onKeyDown={leaveOnEnter}
          placeholder={t('Your name')}
          className={input}
        />
        <SaveStatus state={nameStatus.state} />
      </div>
    </div>

    {/* Running threshold pace — what FTP is to the bike (#716). */}
    {showsPace && (
      <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Running Threshold Pace')}</h3>
        <p className="text-sm text-gray-500 mb-4">
          {t('The pace you could hold for about an hour. Runs are scored against it instead of being estimated from heart rate, and your pace zones are cut from it. Leave it empty and we estimate it from your own maximal efforts — and say so rather than guess when there are not enough of them.')}
          {userProfile?.thresholdPaceSecondsPerKm != null && (
            <span className="ml-1 font-medium text-gray-700">
              {t('Current threshold pace:')}{' '}
              <span className="text-gray-900">{profilePaceInput} /km</span>
            </span>
          )}
        </p>

        <div>
          <div className="relative">
                              <input
              type="text"
              // "decimal" offers a separator key; "numeric" has none, and
              // the colon never shows on a phone keypad (ai-trainer-ops#131).
              inputMode="decimal"
              aria-label={t('Running threshold pace')}
              value={paceInput}
              onChange={(e) => {
              setPaceDraft(e.target.value)
              paceStatus.clearError()
            }}
              onBlur={commitPace}
              onKeyDown={leaveOnEnter}
              placeholder={t('e.g. {example}', { example: '4:10' })}
              className="w-full border border-gray-300 rounded-lg pl-3 pr-14 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">
              min/km
            </span>
          </div>
          <SaveStatus state={paceStatus.state} />
        </div>
      </div>
    )}

    {/* FTP Management */}
    {showsFtp && (
      <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-1">FTP</h3>
        <p className="text-sm text-gray-500 mb-4">
          {t('The power you could hold for about an hour. Rides are scored against it.')}
          {userProfile?.currentFTP != null && (
            <span className="ml-1 font-medium text-gray-700">
              {t('Current FTP:')} <span className="text-gray-900">{userProfile.currentFTP} W</span>
            </span>
          )}
        </p>

        {ftpMsg && <p className={`mb-4 ${note(ftpMsg.type === 'success')}`}>{ftpMsg.text}</p>}

        {/* Advisory only — the athlete's FTP is never overridden. */}
        {ftpPlausibilityWarning && (
          <p role="alert" className="mb-4 text-sm text-gray-700">
            {ftpPlausibilityWarning}
          </p>
        )}

        <div className={isExpertMode ? 'mb-4' : ''}>
          <div className="relative">
                              <input
              type="number"
              min={1}
              /* `aria-label` rather than a `<label htmlFor>` like the two
                 fields above, because this one has no visible label to bind
                 to — the section heading and a placeholder are all it had, so
                 a screen reader announced an unnamed number box
                 (ai-trainer-ops#46). */
              aria-label={t('Current FTP (watts)')}
              value={ftpInput}
              onChange={(e) => {
              setFtpDraft(e.target.value)
              ftpStatus.clearError()
            }}
              onBlur={commitFtp}
              onKeyDown={leaveOnEnter}
              placeholder={userProfile?.currentFTP != null ? String(userProfile.currentFTP) : t('e.g. {example}', { example: 250 })}
              className="w-full border border-gray-300 rounded-lg pl-3 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">W</span>
          </div>
          <SaveStatus state={ftpStatus.state} />
        </div>

        {isExpertMode && (
          <div className="border-t border-gray-100 pt-4">
            <p className="text-sm text-gray-500 mb-3">
              <strong className="text-gray-700">{t('Recalculate metrics')}</strong> —{' '}
              {t('rebuilds TSS, CTL, ATL, and TSB for stored activities using the FTP entered above (or your current FTP if no value is entered). FTP estimates derived automatically from activity data are shown in the Athlete Progression chart on your dashboard.')}
            </p>
            <p className="mb-3 text-sm text-gray-700">
              {slots(
                t('This operation {strong}. All historical training-stress values will be overwritten. A confirmation dialog will appear before any data is changed.'),
                { strong: <strong key="strong">{t('cannot be reversed')}</strong> },
              )}
            </p>
            <button
              onClick={() => void handleRecalculate()}
              disabled={isPipelinePending}
              className={button}
            >
              {isPipelinePending ? t('Recalculating…') : t('Recalculate TSS / ATL / CTL')}
            </button>
          </div>
        )}
      </div>
    )}

    {/* Body weight, optional (ai-trainer-ops#188): a climb target in W/kg needs it. */}
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Body weight')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Optional. The coach needs it for climbs and watts per kilo, and asks for it there. Empty the field to remove it.')}
      </p>
      <div>
        <div className="relative">
                          <input
            type="text"
            inputMode="decimal"
            aria-label={t('Body weight (kg)')}
            value={weightInput}
            onChange={(e) => {
              setWeightDraft(e.target.value)
              weightStatus.clearError()
            }}
            onBlur={commitWeight}
            onKeyDown={leaveOnEnter}
            placeholder={t('e.g. {example}', { example: 70 })}
            className="w-full border border-gray-300 rounded-lg pl-3 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
          />
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">kg</span>
        </div>
        <SaveStatus state={weightStatus.state} />
      </div>
    </div>

    </>
  )
}
