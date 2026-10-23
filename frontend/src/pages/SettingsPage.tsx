import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Download, AlertTriangle, Server, LogOut, User, Zap, RefreshCw, Heart, Footprints, Weight } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { LANGUAGE_CHOICES, slots, useLanguage, useT, type LanguageChoice } from '../i18n'
import AIKeySettings from '../components/AIKeySettings'
import TotpSettings from '../components/TotpSettings'
import SessionSettings from '../components/SessionSettings'
import PasswordSettings from '../components/PasswordSettings'
import AthleteTraitsSettings from '../components/AthleteTraitsSettings'
import CoachReviewConsentCard from '../components/CoachReviewConsentCard'
import MotivationModelSettings from '../components/MotivationModelSettings'
import AthleteModelSettings from '../components/AthleteModelSettings'
import AthletePerformanceModelCard from '../components/AthletePerformanceModelCard'
import ConversationImportSettings from '../components/ConversationImportSettings'
import FitFileUpload from '../components/FitFileUpload'
import HomeLocationSettings from '../components/HomeLocationSettings'
import TrainingAnswersSettings from '../components/TrainingAnswersSettings'
import IntervalsConnect from '../components/IntervalsConnect'
import SetupGuideLink from '../components/SetupGuideLink'
import { setupGuideSection } from '../utils/links'
import { formatPaceFromSeconds, parsePaceToSeconds } from '../utils/pace'
import { resolveActiveSource } from '../hooks/useStravaSync'
import StravaConnect from '../components/StravaConnect'
import StravaImportSummary from '../components/StravaImportSummary'
import SaveStatus from '../components/SaveStatus'
import { useSaveStatus } from '../hooks/useSaveStatus'
import type { AiProvider, UserProfile } from '../store/useAppStore'
import { endSession } from '../services/sessions'
import {
  deleteCurrentUser,
  estimateFTP,
  exportAccountData,
  fetchAIKeyStatus,
  updateCurrentUser,
  type AIKeyStatus,
} from '../services/user'
import { useMetricsPipeline } from '../hooks/useMetricsPipeline'
import { useImportProgress } from '../hooks/useImportProgress'

// Five sections and nothing else without Expert mode (ai-trainer-ops#50). The
// page used to be seventeen cards in one 7,200 px column, ordered by when each
// was built: the display name sat below a provider picker, and an irreversible
// "Recalculate TSS / ATL / CTL" stood between an athlete and their upload.
const SECTIONS = [
  { id: 'profile', title: 'Profile' },
  { id: 'activities', title: 'Activities' },
  { id: 'coach', title: 'Coach & AI' },
  { id: 'account', title: 'Account & security' },
  { id: 'data', title: 'Your data' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

const sectionAnchor = (id: SectionId) => `settings-${id}`
// How long a linked section is looked for: 20 × 100 ms.
const SCROLL_TRIES = 20
const SCROLL_RETRY_MS = 100

/** Side nav from `lg`, tabs that wrap above that. Plain anchors: the browser
 *  already scrolls to them and keeps them in the history.
 *
 *  Wrapping and not a sideways-scrolling strip: a scroll container still
 *  counts its content toward the page's minimum width, so the strip made the
 *  whole page 551 px wide at 390 (seen in the #803 screenshots). */
function SectionNav() {
  const t = useT()
  return (
    <nav
      aria-label={t('Settings sections')}
      className="sticky top-14 z-10 -mx-4 mb-6 bg-gray-100 px-4 py-2 md:top-0 lg:top-8 lg:mx-0 lg:mb-0 lg:w-48 lg:shrink-0 lg:p-0"
    >
      <ul className="flex flex-wrap gap-1 lg:flex-col">
        {SECTIONS.map((section) => (
          <li key={section.id}>
            <a
              href={`#${sectionAnchor(section.id)}`}
              className="block whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-white hover:text-gray-900"
            >
              {t(section.title)}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function Section({ id, children }: { id: SectionId; children: ReactNode }) {
  const t = useT()
  const title = t(SECTIONS.find((section) => section.id === id)!.title)
  return (
    <section
      id={sectionAnchor(id)}
      aria-labelledby={`${sectionAnchor(id)}-heading`}
      // Clears the fixed mobile header plus the sticky tab strip on a jump.
      className="space-y-4 scroll-mt-28 md:scroll-mt-16 lg:scroll-mt-8"
    >
      <h2 id={`${sectionAnchor(id)}-heading`} className="text-lg font-bold text-gray-900">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** Which language the interface speaks (ai-trainer-ops#103 Q27). Applies at
 * once, like every other setting here (#871). */
function LanguageSettings() {
  const t = useT()
  const uiLanguage = useAppStore((s) => s.uiLanguage)
  const setUiLanguage = useAppStore((s) => s.setUiLanguage)
  const labels: Record<LanguageChoice, string> = {
    auto: t('Automatic (browser)'),
    en: 'English',
    de: 'Deutsch',
  }
  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-bold text-gray-900 mb-1">{t('Language')}</h3>
      <p className="text-xs text-gray-500 mb-3">
        {t('The interface follows your browser unless you choose here. Your coach answers in the language you write in.')}
      </p>
      <div role="radiogroup" aria-label={t('Language')} className="flex flex-wrap gap-2">
        {LANGUAGE_CHOICES.map((choice) => (
          <button
            key={choice}
            type="button"
            role="radio"
            aria-checked={uiLanguage === choice}
            onClick={() => setUiLanguage(choice)}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
              uiLanguage === choice
                ? 'border-amber-500 bg-amber-50 text-amber-900'
                : 'border-gray-200 text-gray-700 hover:bg-gray-50'
            }`}
          >
            {labels[choice]}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function SettingsPage() {
  // A link from elsewhere in the app names a section (`/settings#settings-…`,
  // ai-trainer-ops#157). An in-app navigation does not scroll to a hash the way
  // a page load does, so do it once the page is there.
  const { hash } = useLocation()
  useEffect(() => {
    if (!hash) return
    // The section may render only once its data is in, so look again for a
    // moment rather than once.
    let tries = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const find = () => {
      const target = document.getElementById(hash.slice(1))
      if (target) target.scrollIntoView()
      else if (++tries < SCROLL_TRIES) timer = setTimeout(find, SCROLL_RETRY_MS)
    }
    find()
    return () => clearTimeout(timer)
  }, [hash])
  const t = useT()
  const language = useLanguage()
  const {
    authToken,
    userProfile,
    stravaConnection,
    stravaAvailable,
    intervalsConnection,
    lastStravaActivityId,
    stravaAutoSyncEnabled,
    lastIntervalsActivityId,
    intervalsAutoSyncEnabled,
    aiProvider,
    isExpertMode,
    ftpPlausibilityWarning,
    setAiProvider,
    setUserProfile,
    setFtpPlausibilityWarning,
    setStravaAutoSyncEnabled,
    setIntervalsAutoSyncEnabled,
    resetAll,
    logout,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      ftpPlausibilityWarning: s.ftpPlausibilityWarning,
      stravaConnection: s.stravaConnection,
      stravaAvailable: s.stravaAvailable,
      intervalsConnection: s.intervalsConnection,
      lastStravaActivityId: s.lastStravaActivityId,
      stravaAutoSyncEnabled: s.stravaAutoSyncEnabled,
      lastIntervalsActivityId: s.lastIntervalsActivityId,
      intervalsAutoSyncEnabled: s.intervalsAutoSyncEnabled,
      aiProvider: s.aiProvider,
      isExpertMode: s.isExpertMode,
      setAiProvider: s.setAiProvider,
      setUserProfile: s.setUserProfile,
      setFtpPlausibilityWarning: s.setFtpPlausibilityWarning,
      setStravaAutoSyncEnabled: s.setStravaAutoSyncEnabled,
      setIntervalsAutoSyncEnabled: s.setIntervalsAutoSyncEnabled,
      resetAll: s.resetAll,
      logout: s.logout,
    }))
  )

  const profileNameInput = userProfile?.name ?? ''
  const profileFtpInput = userProfile?.currentFTP != null ? String(userProfile.currentFTP) : ''
  const profilePaceInput = formatPaceFromSeconds(userProfile?.thresholdPaceSecondsPerKm)
  const profileWeightInput = userProfile?.weightKg != null ? String(userProfile.weightKg) : ''
  const profileMaxHrInput = userProfile?.maxHeartRate != null ? String(userProfile.maxHeartRate) : ''
  const profileRestingHrInput = userProfile?.restingHeartRate != null ? String(userProfile.restingHeartRate) : ''

  const { updateMetrics, recalculateAll, isPending: isPipelinePending } = useMetricsPipeline()
  const queryClient = useQueryClient()

  const [exportError, setExportError] = useState('')
  // The account deletion asks for the password in the page, not in a native
  // prompt, which showed it in clear text (ai-trainer-ops#131).
  const [deleteConfirming, setDeleteConfirming] = useState(false)
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const nameInput = nameDraft ?? profileNameInput

  // FTP override state
  const [ftpDraft, setFtpDraft] = useState<string | null>(null)
  const ftpInput = ftpDraft ?? profileFtpInput
  const [ftpMsg, setFtpMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Running threshold-pace state (#716)
  const [paceDraft, setPaceDraft] = useState<string | null>(null)
  const paceInput = paceDraft ?? profilePaceInput

  // Body weight, optional (ai-trainer-ops#188)
  const [weightDraft, setWeightDraft] = useState<string | null>(null)
  const weightInput = weightDraft ?? profileWeightInput

  // Heart Rate settings state
  const [maxHrDraft, setMaxHrDraft] = useState<string | null>(null)
  const [restingHrDraft, setRestingHrDraft] = useState<string | null>(null)
  const maxHrInput = maxHrDraft ?? profileMaxHrInput
  const restingHrInput = restingHrDraft ?? profileRestingHrInput
  const [ageInput, setAgeInput] = useState('')
  const [hrMsg, setHrMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const nameStatus = useSaveStatus()
  const ftpStatus = useSaveStatus()
  const paceStatus = useSaveStatus()
  const weightStatus = useSaveStatus()
  const hrStatus = useSaveStatus()
  const providerStatus = useSaveStatus()
  // Which source is actually being read, from the same function the sync hook
  // uses (ai-trainer-ops#49). Recomputing the precedence here would let this
  // sentence and the sync disagree, and an athlete told the wrong thing about
  // where their data comes from has no way to find out otherwise.
  const activeSource = resolveActiveSource({
    intervalsConnection,
    intervalsAutoSyncEnabled,
    stravaConnection,
    stravaAutoSyncEnabled,
  })
  const bothConnected = Boolean(intervalsConnection && stravaConnection)
  const [stravaSyncMsg, setStravaSyncMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [stravaSyncSaving, setStravaSyncSaving] = useState(false)
  const [intervalsSyncMsg, setIntervalsSyncMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [intervalsSyncSaving, setIntervalsSyncSaving] = useState(false)
  // Two-step FTP confirmation after HR save
  const [ftpEstimate, setFtpEstimate] = useState<number | null>(null)
  const [ftpConfirmInput, setFtpConfirmInput] = useState('')

  const importProgress = useImportProgress()

  // Sport-specific fields follow the sports the athlete chose (ai-trainer-ops#93).
  // With none recorded, every field shows, as before.
  const sports = userProfile?.sports ?? []
  const showsFtp = sports.length === 0 || sports.includes('cycling')
  const showsPace = sports.length === 0 || sports.includes('running')

  /** Settings save themselves (ai-trainer-ops#93): a field when the athlete
   *  leaves it, a choice when it is made. This section alone had five save
   *  buttons, and a value typed into one card stayed unsaved when the athlete
   *  pressed the button of another. */
  const saveFields = async (
    status: ReturnType<typeof useSaveStatus>,
    updates: Parameters<typeof updateCurrentUser>[1],
    then: () => void
  ): Promise<UserProfile | undefined> => {
    if (!authToken || !userProfile) return undefined
    let saved: UserProfile | undefined
    await status.run(async () => {
      const updated = await updateCurrentUser(authToken, updates)
      setUserProfile(updated.profile)
      setFtpPlausibilityWarning(updated.ftpPlausibilityWarning)
      saved = updated.profile
      then()
    })
    return saved
  }

  // The draft goes once its value is stored, unless the athlete has typed
  // something else meanwhile: that is a new edit, and its own blur saves it.
  const dropDraftIfSent =
    (set: (update: (current: string | null) => string | null) => void, sent: string) => () =>
      set((current) => (current === sent ? null : current))

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

  // Step 1 of the expert flow: a saved heart rate gives an FTP estimate to
  // confirm. It feeds "Confirm & Recalculate", which rewrites history and is
  // expert-only (ai-trainer-ops#50); without Expert mode a save is a save.
  const estimateFtpFromHr = async (profile: UserProfile | undefined) => {
    if (!authToken || !profile || !isExpertMode) return
    setFtpEstimate(null)
    setFtpConfirmInput('')
    setHrMsg(null)
    try {
      const result = await estimateFTP(authToken, {
        maxHeartRate: profile.maxHeartRate,
        restingHeartRate: profile.restingHeartRate,
      })
      if (result.estimatedFTP !== null && result.estimatedFTP !== undefined) {
        setFtpEstimate(result.estimatedFTP)
        setFtpConfirmInput(String(result.estimatedFTP))
        setHrMsg({ type: 'success', text: t('Review your estimated FTP below and confirm to rebuild metrics.') })
      } else {
        setHrMsg({ type: 'success', text: t('No activity data available yet to estimate FTP — sync Strava first.') })
      }
    } catch {
      setHrMsg({ type: 'error', text: t('Could not estimate FTP from heart rate. Please try again.') })
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

  const chooseProvider = (provider: AiProvider) => {
    if (!authToken || provider === aiProvider) return
    void providerStatus.run(async () => {
      await updateCurrentUser(authToken, { aiProvider: provider })
      setAiProvider(provider)
    })
  }

  // A field saves when it is left; Enter leaves it.
  const leaveOnEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur()
  }

  const handleRecalculate = async () => {
    if (!authToken) return
    // A value still in the field is used as typed. The click blurs the field
    // and starts its save, but that save may not have landed when the
    // recalculation reads the stored FTP.
    const overrideInput = ftpDraft !== null ? ftpInput.trim() : ''
    const parsed = overrideInput ? parseInt(overrideInput, 10) : undefined
    if (parsed !== undefined && (isNaN(parsed) || parsed <= 0)) {
      setFtpMsg({ type: 'error', text: t('Please enter a valid positive FTP value in watts.') })
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

    setFtpMsg(null)
    try {
      const result = parsed !== undefined
        ? await updateMetrics({ currentFTP: parsed })
        : await recalculateAll()
      if (parsed !== undefined) setFtpDraft(null)
      queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
      setFtpMsg({
        type: 'success',
        text: result.updated === 1
          ? t('Recalculated 1 activity using FTP {ftp} W.', { ftp: result.ftpUsed })
          : t('Recalculated {count} activities using FTP {ftp} W.', { count: result.updated, ftp: result.ftpUsed }),
      })
      setTimeout(() => setFtpMsg(null), 5000)
    } catch (e) {
      setFtpMsg({ type: 'error', text: t(e instanceof Error ? e.message : 'Recalculation failed. Please try again.') })
    }
  }

  const handleExportAccount = async () => {
    if (!authToken) return
    setExportError('')
    try {
      const data = await exportAccountData(authToken)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ai-trainer-export-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setExportError(t('Export failed. Please try again.'))
    }
  }

  const handleReset = () => {
    if (!authToken) return
    // Says "account" and not "data": this deletes the account itself, and the
    // old wording understated what the button does.
    if (!window.confirm(t('Are you sure? This deletes your account, all your data and your training plan. It cannot be undone.'))) return
    // The backend requires the password since ai-trainer-ops#35.
    setDeleteError(null)
    setDeleteConfirming(true)
  }

  const cancelDelete = () => {
    setDeleteConfirming(false)
    setDeletePassword('')
    setDeleteError(null)
  }

  const confirmDelete = async () => {
    if (!authToken || !deletePassword) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteCurrentUser(authToken, deletePassword)
    } catch (e) {
      // A wrong password keeps the session: the server answers 403, not 401.
      setDeleteError(t(e instanceof Error ? e.message : 'The account could not be deleted.'))
      setDeleting(false)
      return
    }
    resetAll()
  }

  // Step 2: Confirm the FTP and trigger a full metrics recalculation.
  const handleConfirmFTP = async () => {
    if (!authToken) return
    const parsed = parseInt(ftpConfirmInput, 10)
    if (isNaN(parsed) || parsed <= 0) {
      setHrMsg({ type: 'error', text: t('Please enter a valid positive FTP value in watts.') })
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

    setHrMsg(null)
    try {
      const result = await updateMetrics({ currentFTP: parsed })
      setFtpEstimate(null)
      setFtpConfirmInput('')
      setMaxHrDraft(null)
      setRestingHrDraft(null)
      setAgeInput('')
      queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
      setHrMsg({
        type: 'success',
        text: result.updated === 1
          ? t('Done! Recalculated 1 activity using FTP {ftp} W.', { ftp: result.ftpUsed })
          : t('Done! Recalculated {count} activities using FTP {ftp} W.', { count: result.updated, ftp: result.ftpUsed }),
      })
      setTimeout(() => setHrMsg(null), 6000)
    } catch (e) {
      setHrMsg({ type: 'error', text: t(e instanceof Error ? e.message : 'Metrics recalculation failed. Please try again.') })
    }
  }

  const handleLogout = () => endSession(logout)

  const handleStravaAutoSyncChange = async (enabled: boolean) => {
    if (!authToken) return
    const previous = stravaAutoSyncEnabled
    setStravaAutoSyncEnabled(enabled)
    setStravaSyncSaving(true)
    setStravaSyncMsg(null)
    try {
      await updateCurrentUser(authToken, { stravaAutoSyncEnabled: enabled })
      setStravaSyncMsg({
        type: 'success',
        text: enabled ? t('Automatic Strava sync enabled.') : t('Automatic Strava sync disabled.'),
      })
      setTimeout(() => setStravaSyncMsg(null), 3000)
    } catch {
      setStravaAutoSyncEnabled(previous)
      setStravaSyncMsg({ type: 'error', text: t('Could not save Strava sync setting. Please try again.') })
    } finally {
      setStravaSyncSaving(false)
    }
  }

  const handleIntervalsAutoSyncChange = async (enabled: boolean) => {
    if (!authToken) return
    const previous = intervalsAutoSyncEnabled
    setIntervalsAutoSyncEnabled(enabled)
    setIntervalsSyncSaving(true)
    setIntervalsSyncMsg(null)
    try {
      await updateCurrentUser(authToken, { intervalsAutoSyncEnabled: enabled })
      setIntervalsSyncMsg({
        type: 'success',
        text: enabled ? t('Automatic Intervals.icu sync enabled.') : t('Automatic Intervals.icu sync disabled.'),
      })
      setTimeout(() => setIntervalsSyncMsg(null), 3000)
    } catch {
      setIntervalsAutoSyncEnabled(previous)
      setIntervalsSyncMsg({ type: 'error', text: t('Could not save Intervals.icu sync setting. Please try again.') })
    } finally {
      setIntervalsSyncSaving(false)
    }
  }

  // The hints used to be hard-coded here as "GPT-4o mini" / "Gemini 2.0 Flash"
  // — stale, and disagreeing with AIKeySettings, which claimed "Gemini 2.5
  // Flash" for the same backend. Both were wrong: it has been
  // gemini-3.5-flash-lite since #511. The backend now reports what it runs
  // (#691). Same query key as AIKeySettings, so react-query serves both from
  // one request.
  const { data: keyStatus } = useQuery<AIKeyStatus>({
    queryKey: ['ai-key-status'],
    queryFn: () => fetchAIKeyStatus(authToken!),
    enabled: !!authToken,
  })

  const providers: { value: AiProvider; label: string; hint?: string; placeholder: string }[] = [
    { value: 'openai', label: 'OpenAI', hint: keyStatus?.openaiModel, placeholder: 'sk-...' },
    { value: 'gemini', label: 'Google Gemini', hint: keyStatus?.geminiModel, placeholder: 'AIza...' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('Settings')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('Your profile, where your activities come from, the coach, and your account')}</p>
      </div>

      <div className="lg:flex lg:items-start lg:gap-8">
        <SectionNav />
        <div className="min-w-0 max-w-2xl flex-1 space-y-10">
        <Section id="profile">
          <LanguageSettings />
          {/* The onboarding answers (ai-trainer-ops#202). */}
          <TrainingAnswersSettings />
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Name')}</h3>
            <div>
              <label htmlFor="settings-display-name" className="block text-xs font-semibold text-gray-700 mb-1">
                <span className="flex items-center gap-1"><User size={13} /> {t('Display Name')}</span>
              </label>
              <input
                type="text"
                id="settings-display-name"
                value={nameInput}
                onChange={(e) => {
                  setNameDraft(e.target.value)
                  nameStatus.clearError()
                }}
                onBlur={commitName}
                onKeyDown={leaveOnEnter}
                placeholder={t('Your name')}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              />
              <SaveStatus state={nameStatus.state} />
            </div>
          </div>

          {/* Running threshold pace — what FTP is to the bike (#716). */}
          {showsPace && (
            <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
              <h3 className="text-base font-bold text-gray-900 mb-1">{t('Running Threshold Pace')}</h3>
              <p className="text-xs text-gray-500 mb-4">
                {t('The pace you could hold for about an hour. Runs are scored against it instead of being estimated from heart rate, and your pace zones are cut from it. Leave it empty and we estimate it from your own maximal efforts — and say so rather than guess when there are not enough of them.')}
                {userProfile?.thresholdPaceSecondsPerKm != null && (
                  <span className="ml-1 font-medium text-gray-700">
                    {t('Current threshold pace:')}{' '}
                    <span className="text-purple-700">{profilePaceInput} /km</span>
                  </span>
                )}
              </p>

              <div>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
                    <Footprints size={14} />
                  </span>
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
                    className="w-full border border-gray-300 rounded-lg pl-8 pr-14 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
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
              <h3 className="text-base font-bold text-gray-900 mb-1">FTP</h3>
              <p className="text-xs text-gray-500 mb-4">
                {t('The power you could hold for about an hour. Rides are scored against it.')}
                {userProfile?.currentFTP != null && (
                  <span className="ml-1 font-medium text-gray-700">
                    {t('Current FTP:')} <span className="text-purple-700">{userProfile.currentFTP} W</span>
                  </span>
                )}
              </p>

              {ftpMsg && (
                <div
                  className={`rounded-lg px-4 py-3 text-sm mb-4 ${
                    ftpMsg.type === 'success'
                      ? 'bg-green-50 border border-green-200 text-green-700'
                      : 'bg-red-50 border border-red-200 text-red-700'
                  }`}
                >
                  {ftpMsg.text}
                </div>
              )}

              {/* Advisory only — the athlete's FTP is never overridden. */}
              {ftpPlausibilityWarning && (
                <div
                  role="alert"
                  className="rounded-lg px-4 py-3 text-sm mb-4 bg-amber-50 border border-amber-200 text-amber-800"
                >
                  {ftpPlausibilityWarning}
                </div>
              )}

              <div className={isExpertMode ? 'mb-4' : ''}>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
                    <Zap size={14} />
                  </span>
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
                    className="w-full border border-gray-300 rounded-lg pl-8 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">W</span>
                </div>
                <SaveStatus state={ftpStatus.state} />
              </div>

              {isExpertMode && (
                <div className="border-t border-gray-100 pt-4">
                  <p className="text-xs text-gray-500 mb-3">
                    <strong className="text-gray-700">{t('Recalculate metrics')}</strong> —{' '}
                    {t('rebuilds TSS, CTL, ATL, and TSB for stored activities using the FTP entered above (or your current FTP if no value is entered). FTP estimates derived automatically from activity data are shown in the Athlete Progression chart on your dashboard.')}
                  </p>
                  <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-3 flex gap-2">
                    <AlertTriangle size={15} className="text-yellow-600 shrink-0 mt-0.5" />
                    <p className="text-xs text-yellow-700">
                      {slots(
                        t('This operation {strong}. All historical training-stress values will be overwritten. A confirmation dialog will appear before any data is changed.'),
                        { strong: <strong key="strong">{t('cannot be reversed')}</strong> },
                      )}
                    </p>
                  </div>
                  <button
                    onClick={() => void handleRecalculate()}
                    disabled={isPipelinePending}
                    className="flex items-center gap-1.5 bg-purple-600 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-purple-700 disabled:opacity-50"
                  >
                    <RefreshCw size={15} className={isPipelinePending ? 'animate-spin' : ''} />
                    {isPipelinePending ? t('Recalculating…') : t('Recalculate TSS / ATL / CTL')}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Body weight, optional (ai-trainer-ops#188): a climb target in W/kg needs it. */}
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Body weight')}</h3>
            <p className="text-xs text-gray-500 mb-4">
              {t('Optional. The coach needs it for climbs and watts per kilo, and asks for it there. Empty the field to remove it.')}
            </p>
            <div>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
                  <Weight size={14} />
                </span>
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
                  className="w-full border border-gray-300 rounded-lg pl-8 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">kg</span>
              </div>
              <SaveStatus state={weightStatus.state} />
            </div>
          </div>

          {/* Heart Rate Settings */}
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Heart Rate Settings')}</h3>
            <p className="text-xs text-gray-500 mb-1">
              {t('Max HR and resting HR are used for HR-based FTP estimation and training zones.')}
            </p>
            {(userProfile?.maxHeartRate != null || userProfile?.restingHeartRate != null) && (
              <p className="text-xs text-gray-500 mb-4">
                {t('Current:')}{' '}
                {userProfile.maxHeartRate != null && (
                  <span className="font-medium text-gray-700 mr-2">{t('Max HR')}: <span className="text-red-600">{userProfile.maxHeartRate} bpm</span></span>
                )}
                {userProfile.restingHeartRate != null && (
                  <span className="font-medium text-gray-700 mr-2">{t('Resting HR')}: <span className="text-blue-600">{userProfile.restingHeartRate} bpm</span></span>
                )}
              </p>
            )}

            {hrMsg && (
              <div
                className={`rounded-lg px-4 py-3 text-sm mb-4 ${
                  hrMsg.type === 'success'
                    ? 'bg-green-50 border border-green-200 text-green-700'
                    : 'bg-red-50 border border-red-200 text-red-700'
                }`}
              >
                {hrMsg.text}
              </div>
            )}

            <div className="space-y-3 mb-4">
              <div>
                <label htmlFor="settings-max-hr" className="block text-xs font-semibold text-gray-700 mb-1">
                  <span className="flex items-center gap-1"><Heart size={13} /> {t('Max Heart Rate (bpm)')}</span>
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
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                />
                {!maxHrInput && (
                  <div className="mt-2">
                    <label className="block text-xs text-gray-500 mb-1">
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
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    />
                    {ageInput && (() => {
                      const parsedAge = parseInt(ageInput, 10)
                      const est = Number.isFinite(parsedAge) && parsedAge >= 10
                        ? Math.max(100, 220 - parsedAge)
                        : undefined
                      return est !== undefined ? (
                        <p className="text-xs text-amber-700 mt-1">
                          {t('Estimated Max HR: {bpm} bpm', { bpm: est })}
                        </p>
                      ) : null
                    })()}
                  </div>
                )}
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1">
                  <span className="flex items-center gap-1"><Heart size={13} /> {t('Resting Heart Rate (bpm)')}</span>
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
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                />
              </div>
            </div>

            <SaveStatus state={hrStatus.state} />

            {/* Step 2: FTP confirmation panel */}
            {isExpertMode && ftpEstimate !== null && (
              <div className="mt-4 border border-amber-200 bg-amber-50 rounded-xl p-4 space-y-3">
                <p className="text-sm font-semibold text-gray-800">
                  {t('Step 2 — Confirm FTP before rebuilding metrics')}
                </p>
                <p className="text-xs text-gray-600">
                  {slots(
                    t('Based on your activity history, your estimated FTP is {ftp}. You can adjust this value before recalculating all training-stress metrics (TSS, ATL, CTL, TSB).'),
                    { ftp: <strong key="ftp" className="text-purple-700">{ftpEstimate} W</strong> },
                  )}
                </p>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
                      <Zap size={14} />
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={ftpConfirmInput}
                      onChange={(e) => setFtpConfirmInput(e.target.value)}
                      className="w-full border border-gray-300 rounded-lg pl-8 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">W</span>
                  </div>
                  <button
                    onClick={() => void handleConfirmFTP()}
                    disabled={isPipelinePending || !ftpConfirmInput.trim()}
                    className="flex items-center gap-1.5 bg-purple-600 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-purple-700 disabled:opacity-50"
                  >
                    <RefreshCw size={15} className={isPipelinePending ? 'animate-spin' : ''} />
                    {isPipelinePending ? t('Recalculating…') : t('Confirm & Recalculate')}
                  </button>
                </div>
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 flex gap-2">
                  <AlertTriangle size={14} className="text-yellow-600 shrink-0 mt-0.5" />
                  <p className="text-xs text-yellow-700">
                    {slots(t('This will overwrite all historical training-stress values and {strong}.'), {
                      strong: <strong key="strong">{t('cannot be reversed')}</strong>,
                    })}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Editable training location behind the weather forecast (#495) */}
          <HomeLocationSettings />
        </Section>

        <Section id="activities">
          {/* Data Sources */}
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Data Sources')}</h3>
            <p className="text-xs text-gray-500">
              {t('Choose how AI Trainer imports your training history. Automatic sources are convenient, while FIT files keep the original workout data directly in your hands.')}
            </p>
            {/* One plain line, no tinted box: the section had six text styles and
                four coloured boxes (owner's review, 2026-10-23: "einheitliches
                sauberes Design, eher minimalistisch"). */}
            <p className="mt-2 text-xs text-gray-500">
                {/* The backend's URL used to be printed here (ai-trainer-ops#50).
                    It is a deployment detail no athlete can act on, and on a local
                    install it read "the backend at http://localhost:8000" — which
                    tells a cyclist nothing and makes the product look unfinished.

                    "at rest" and not "never leave this server", which is what this
                    said for one commit until review caught it. The keys do leave:
                    the intervals.icu key goes to intervals.icu, the Strava token to
                    Strava, the athlete's own provider key to OpenAI or Gemini on
                    every call. A privacy sentence on a settings page is something
                    an athlete relies on, so it may only claim what
                    `EncryptedString` actually provides.

                    The Strava sentence said a paid Strava subscription might be
                    needed. What stops testers is that intervals.icu does not pass
                    on rides it got only from Strava (owner's review, 2026-10-23). */}
                {t('Your keys are stored encrypted at rest. Rides that reach intervals.icu only through Strava do not reach us: connect your device to intervals.icu directly. FIT upload stays available either way.')}
            </p>
            <p className="mt-2">
              <SetupGuideLink
                href={setupGuideSection(language, 'intervals')}
                label={t('How your rides get in through intervals.icu')}
              />
            </p>

            <div className="mt-2 divide-y divide-gray-100">
              {/* intervals.icu first (ai-trainer-ops#20): the primary path, with
                  Strava as a convenience on top. */}
              <div className="py-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">Intervals.icu</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {t('Direct activity import using your Intervals.icu API key.')}
                    </p>
                  </div>
                  {/* The state is in the block below; which source is read only
                      matters with both connected (ai-trainer-ops#49). */}
                  {bothConnected && activeSource === 'intervals' && (
                    <span className="shrink-0 text-xs font-medium text-amber-700">{t('Active source')}</span>
                  )}
                </div>
                <IntervalsConnect />
                {/* No "Automatic sync: On" line: the switch below says it (owner's
                    review, 2026-10-23). */}
                {isExpertMode && (
                  <p className="text-xs text-gray-500">
                    {t('Last sync cursor: {cursor}', { cursor: lastIntervalsActivityId ?? t('none yet') })}
                  </p>
                )}
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm text-gray-900">{t('Turn on automatic sync with Intervals.icu')}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {t('When enabled, AI Trainer checks for new Intervals.icu activities while the app is open.')}
                    </p>
                    {intervalsSyncMsg && (
                      <p className={`text-xs mt-2 ${intervalsSyncMsg.type === 'success' ? 'text-green-700' : 'text-red-600'}`}>
                        {intervalsSyncMsg.text}
                      </p>
                    )}
                  </div>
                  <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
                    <input
                      type="checkbox"
                      className="peer sr-only"
                      checked={intervalsAutoSyncEnabled}
                      disabled={intervalsSyncSaving}
                      aria-label={t('Turn on automatic sync with Intervals.icu')}
                      onChange={(e) => void handleIntervalsAutoSyncChange(e.target.checked)}
                    />
                    <span className="h-6 w-11 rounded-full bg-gray-200 transition peer-checked:bg-amber-500 peer-disabled:opacity-50" />
                    <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white shadow transition peer-checked:translate-x-5" />
                  </label>
                </div>
              </div>

              {/* Off on production (owner's decision, 2026-10-21): athletes connect
                  Strava to intervals.icu instead. An existing connection stays
                  visible, so it can still be disconnected. */}
              {(stravaAvailable || stravaConnection) && (
                <div className="py-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-gray-900">Strava</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {t('OAuth-based automatic import for connected Strava accounts.')}
                      </p>
                    </div>
                    {bothConnected && activeSource === 'strava' && (
                      <span className="shrink-0 text-xs font-medium text-amber-700">{t('Active source')}</span>
                    )}
                  </div>
                  {/* Said once, where an athlete with both connections will look for
                      it: the badge above says which one is read, and this says why
                      the other one is not (ai-trainer-ops#49). Without it, "Connected"
                      on both and syncing from one reads as a bug. */}
                  {activeSource === 'intervals' && stravaConnection && (
                    <p className="text-xs text-gray-500">
                      {t('Intervals.icu is the primary source, so activities are read from there. Strava stays connected and is used if you turn off Intervals.icu sync.')}
                    </p>
                  )}
                  <StravaConnect />
                  {isExpertMode && (
                    <p className="text-xs text-gray-500">
                      {t('Last sync cursor: {cursor}', { cursor: lastStravaActivityId ?? t('none yet') })}
                    </p>
                  )}
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm text-gray-900">{t('Turn on automatic sync with Strava')}</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {t('When enabled, AI Trainer checks for new Strava activities while the app is open.')}
                      </p>
                      {stravaSyncMsg && (
                        <p className={`text-xs mt-2 ${stravaSyncMsg.type === 'success' ? 'text-green-700' : 'text-red-600'}`}>
                          {stravaSyncMsg.text}
                        </p>
                      )}
                    </div>
                    <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
                      <input
                        type="checkbox"
                        className="peer sr-only"
                        checked={stravaAutoSyncEnabled}
                        disabled={stravaSyncSaving}
                        aria-label={t('Turn on automatic sync with Strava')}
                        onChange={(e) => void handleStravaAutoSyncChange(e.target.checked)}
                      />
                      <span className="h-6 w-11 rounded-full bg-gray-200 transition peer-checked:bg-amber-500 peer-disabled:opacity-50" />
                      <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white shadow transition peer-checked:translate-x-5" />
                    </label>
                  </div>
                </div>
              )}

              <div className="pt-4 space-y-1">
                <p className="text-sm font-semibold text-gray-900">{t('FIT files')}</p>
                <FitFileUpload embedded />
              </div>
            </div>

            {importProgress.status !== 'idle' && (
              <div className="mt-4 border border-gray-100 rounded-xl p-4 bg-gray-50">
                <p className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">{t('Strava Activity Analysis')}</p>
                {importProgress.status === 'running' && (() => {
                  const pct = importProgress.total > 0
                    ? Math.round((importProgress.processed / importProgress.total) * 100)
                    : null
                  return (
                    <>
                      <div className="w-full bg-gray-200 rounded-full h-2 mb-1">
                        <div
                          className="bg-amber-500 h-2 rounded-full transition-all duration-300"
                          style={{ width: pct !== null ? `${pct}%` : '10%' }}
                        />
                      </div>
                      <p className="text-xs text-gray-500">
                        {pct !== null
                          ? t('Processed activities: {processed} / {total} ({pct}%) · {imported} imported', {
                              processed: importProgress.processed,
                              total: importProgress.total,
                              pct,
                              imported: importProgress.imported,
                            })
                          : t('Fetching activity list…')}
                      </p>
                    </>
                  )
                })()}
                {importProgress.status === 'done' && (
                  <div className="space-y-3">
                    <p className="text-sm text-gray-700">
                      {t('{imported} imported, {skipped} skipped', { imported: importProgress.imported, skipped: importProgress.skipped })}
                    </p>
                    <StravaImportSummary progress={importProgress} compact />
                  </div>
                )}
                {importProgress.status === 'error' && (
                  <p className="text-xs text-red-600">{t('Import failed: {error}', { error: importProgress.error || t('unknown error') })}</p>
                )}
              </div>
            )}
          </div>
        </Section>

        <Section id="coach">
          {/* Per-user AI API key (BYOK) */}
          <AIKeySettings />

          {isExpertMode && (
            <>
              {/* AI Provider */}
              <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
                <h3 className="text-base font-bold text-gray-900 mb-1">{t('AI Provider')}</h3>
                <p className="text-xs text-gray-500 mb-4">
                  {t('Choose which server-side model powers your training plan and coach chat.')}
                </p>

                <div className="grid grid-cols-2 gap-3 mb-4">
                  {providers.map((p) => (
                    <button
                      key={p.value}
                      type="button"
                      onClick={() => chooseProvider(p.value)}
                      aria-pressed={aiProvider === p.value}
                      className={`flex flex-col items-start px-4 py-3 rounded-xl border-2 text-left transition-all ${
                        aiProvider === p.value
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <span className="font-semibold text-sm text-gray-900">{p.label}</span>
                      <span className="text-xs text-gray-500 font-mono">{p.hint ?? '—'}</span>
                    </button>
                  ))}
                </div>

                {/*
                  This used to warn that "the browser no longer stores or sends provider
                  API keys". That predates BYOK and is now the opposite of true — the
                  "Your AI Provider Key" section at the bottom of this page sends one.
                  Read in order, the page told you there was nothing to enter and then
                  offered the field 470 lines later, which is exactly how long it took
                  someone to give up looking for it.
                */}
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex gap-2">
                  <Server size={16} className="text-blue-600 shrink-0 mt-0.5" />
                  <p className="text-xs text-blue-700">
                    {slots(
                      t("This picks the model only. Requests run on the server's key by default — to have them billed to your own account instead, add a key under {link} above."),
                      {
                        link: (
                          <a key="link" href="#your-ai-provider-key" className="font-semibold underline">
                            {t('Your AI Provider Key')}
                          </a>
                        ),
                      },
                    )}
                  </p>
                </div>

                <SaveStatus state={providerStatus.state} />
              </div>
            </>
          )}

          {/* What the athlete trains FOR (#562/#567). Ahead of the learned traits
              on purpose: everything below describes the athlete, this decides what
              all of it is in service of. */}
          <MotivationModelSettings />

          {/* Learned athlete traits */}
          <AthleteTraitsSettings />

          {isExpertMode && (
            <>
              {/* Import historical coach conversations */}
              <ConversationImportSettings />

              {/* Long-term structured athlete model (#384) */}
              <AthleteModelSettings />

              {/* Deterministic performance model, limiter & hypotheses (#481) */}
              <AthletePerformanceModelCard />
            </>
          )}
        </Section>

        <Section id="account">
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Account')}</h3>
            <p className="text-xs text-gray-500 mb-4">{t('Signed in as {email}.', { email: userProfile?.email ?? t('unknown') })}</p>

            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50"
            >
              <LogOut size={15} /> {t('Sign Out')}
            </button>
          </div>

          {/* Ending sessions elsewhere (#704). Immediately after the Account card
              on purpose: this is where someone looks when they want to be signed
              out, and the first person to go looking for it clicked "Sign Out"
              above instead. */}
          <SessionSettings />

          {/* Next to the sessions, because a new password ends them all
              (ai-trainer-ops#172). */}
          <PasswordSettings />

          <TotpSettings />
        </Section>

        <Section id="data">
          {/* Your data (ai-trainer-ops#6): export before the danger zone, so the
              way to keep a copy sits right above the way to delete everything.
              The coach review consent (ai-trainer-ops#136) comes first, shown
              only while the opt-in is switched on. */}
          <CoachReviewConsentCard />
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-bold text-gray-900 mb-1">{t('Your Data')}</h3>
            <p className="text-xs text-gray-500 mb-4">
              {t('Download everything stored about your account as one JSON file. Passwords, keys and access tokens are not included.')}
            </p>
            <button
              onClick={handleExportAccount}
              className="flex items-center gap-1.5 bg-gray-50 border border-gray-200 text-gray-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-gray-100"
            >
              <Download size={15} /> {t('Export My Data')}
            </button>
            {exportError && <p className="text-xs text-red-600 mt-2">{exportError}</p>}
          </div>

          {/* Danger zone */}
          <div className="bg-white rounded-2xl shadow-xs border border-red-100 p-6">
            <h3 className="text-base font-bold text-red-600 mb-1">{t('Danger Zone')}</h3>
            <p className="text-xs text-gray-500 mb-4">
              {t('Permanently delete all your data and start over from scratch.')}
            </p>
            {!deleteConfirming && (
              <button
                onClick={handleReset}
                className="flex items-center gap-1.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100"
              >
                <Trash2 size={15} /> {t('Reset All Data')}
              </button>
            )}
            {deleteConfirming && (
              <form
                className="space-y-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  void confirmDelete()
                }}
              >
                <label htmlFor="delete-account-password" className="block text-xs font-semibold text-gray-700">
                  {t('Enter your password to confirm.')}
                </label>
                <input
                  id="delete-account-password"
                  type="password"
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  autoComplete="current-password"
                  autoFocus
                />
                {deleteError && <p className="text-xs text-red-600">{deleteError}</p>}
                <div className="flex gap-2">
                  <button
                    type="submit"
                    disabled={deleting || !deletePassword}
                    className="flex items-center gap-1.5 bg-red-600 text-white rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                  >
                    <Trash2 size={15} /> {t('Delete my account')}
                  </button>
                  <button type="button" onClick={cancelDelete} className="text-sm text-gray-500 hover:text-gray-700 px-2">
                    {t('Cancel')}
                  </button>
                </div>
              </form>
            )}
          </div>
        </Section>
        </div>
      </div>
    </div>
  )
}
