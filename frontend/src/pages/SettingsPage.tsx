import { useState } from 'react'
import { Save, Trash2, AlertTriangle, Server, LogOut, User, Zap, RefreshCw, Heart } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import StravaConnect from '../components/StravaConnect'
import type { AiProvider } from '../store/useAppStore'
import { BACKEND_URL, AUTHELIA_URL } from '../services/api'
import { deleteCurrentUser, estimateFTP, updateCurrentUser } from '../services/user'
import { useMetricsPipeline } from '../hooks/useMetricsPipeline'
import { useImportProgress } from '../hooks/useImportProgress'

export default function SettingsPage() {
  const {
    authToken,
    userProfile,
    aiProvider,
    setAiProvider,
    setUserProfile,
    resetAll,
    logout,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      aiProvider: s.aiProvider,
      setAiProvider: s.setAiProvider,
      setUserProfile: s.setUserProfile,
      resetAll: s.resetAll,
      logout: s.logout,
    }))
  )

  const { updateMetrics, recalculateAll, isPending: isPipelinePending } = useMetricsPipeline()

  const [selectedProvider, setSelectedProvider] = useState<AiProvider>(aiProvider)
  const [savedMsg, setSavedMsg] = useState('')
  const [nameInput, setNameInput] = useState(userProfile?.name ?? '')

  // FTP override state
  const [ftpInput, setFtpInput] = useState('')
  const [ftpSaving, setFtpSaving] = useState(false)
  const [ftpMsg, setFtpMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Heart Rate settings state
  const [maxHrInput, setMaxHrInput] = useState('')
  const [restingHrInput, setRestingHrInput] = useState('')
  const [thresholdHrInput, setThresholdHrInput] = useState('')
  const [ageInput, setAgeInput] = useState('')
  const [hrMsg, setHrMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [hrWorking, setHrWorking] = useState(false)
  // Two-step FTP confirmation after HR save
  const [ftpEstimate, setFtpEstimate] = useState<number | null>(null)
  const [ftpConfirmInput, setFtpConfirmInput] = useState('')

  const importProgress = useImportProgress()

  const saveAI = async () => {
    if (!authToken) return

    await updateCurrentUser(authToken, { aiProvider: selectedProvider })
    setAiProvider(selectedProvider)
    setSavedMsg('AI settings saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const saveName = async () => {
    if (!authToken || !userProfile) return
    const trimmed = nameInput.trim()
    if (!trimmed) return
    const updated = await updateCurrentUser(authToken, { name: trimmed })
    setUserProfile(updated.profile)
    setSavedMsg('Name saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const saveFTP = async () => {
    if (!authToken || !userProfile) return
    const parsed = parseInt(ftpInput, 10)
    if (isNaN(parsed) || parsed <= 0) {
      setFtpMsg({ type: 'error', text: 'Please enter a valid positive FTP value in watts.' })
      return
    }
    setFtpSaving(true)
    setFtpMsg(null)
    try {
      const updated = await updateCurrentUser(authToken, { currentFTP: parsed })
      setUserProfile(updated.profile)
      setFtpInput('')
      setFtpMsg({ type: 'success', text: `FTP updated to ${parsed} W.` })
      setTimeout(() => setFtpMsg(null), 3000)
    } catch {
      setFtpMsg({ type: 'error', text: 'Failed to save FTP. Please try again.' })
    } finally {
      setFtpSaving(false)
    }
  }

  const handleRecalculate = async () => {
    if (!authToken) return
    const parsed = ftpInput.trim() ? parseInt(ftpInput, 10) : undefined
    if (parsed !== undefined && (isNaN(parsed) || parsed <= 0)) {
      setFtpMsg({ type: 'error', text: 'Please enter a valid positive FTP value in watts.' })
      return
    }
    const confirmed = window.confirm(
      '⚠️ Recalculate TSS, ATL, CTL for all rides?\n\n' +
        'This will overwrite all historical training stress values using ' +
        (parsed ? `${parsed} W` : 'your current FTP') +
        ' as the reference. This operation cannot be reversed.\n\nContinue?'
    )
    if (!confirmed) return

    setFtpMsg(null)
    try {
      const result = parsed !== undefined
        ? await updateMetrics({ currentFTP: parsed })
        : await recalculateAll()
      if (parsed !== undefined) setFtpInput('')
      setFtpMsg({
        type: 'success',
        text: `Recalculated ${result.updated} rides using FTP ${result.ftpUsed} W.`,
      })
      setTimeout(() => setFtpMsg(null), 5000)
    } catch (e) {
      setFtpMsg({ type: 'error', text: e instanceof Error ? e.message : 'Recalculation failed. Please try again.' })
    }
  }

  const handleReset = async () => {
    if (!authToken) return
    if (window.confirm('Are you sure? This will delete all your data and training plan.')) {
      await deleteCurrentUser(authToken)
      resetAll()
    }
  }

  // Step 1: Save new HR values and retrieve best FTP estimate.
  const handleSaveHR = async () => {
    if (!authToken || !userProfile) return

    // Resolve Max HR from direct input or age estimate (min age 10 to avoid unrealistic values).
    const parsedMaxHr = maxHrInput.trim() ? parseInt(maxHrInput, 10) : undefined
    const parsedAge = ageInput.trim() ? parseInt(ageInput, 10) : undefined
    const resolvedMaxHr: number | undefined =
      parsedMaxHr ?? (parsedAge !== undefined && parsedAge >= 10 ? Math.max(100, 220 - parsedAge) : undefined)

    const parsedRestingHr = restingHrInput.trim() ? parseInt(restingHrInput, 10) : undefined
    const parsedThresholdHr = thresholdHrInput.trim() ? parseInt(thresholdHrInput, 10) : undefined

    if (
      (parsedMaxHr !== undefined && (isNaN(parsedMaxHr) || parsedMaxHr <= 0)) ||
      (parsedRestingHr !== undefined && (isNaN(parsedRestingHr) || parsedRestingHr <= 0)) ||
      (parsedThresholdHr !== undefined && (isNaN(parsedThresholdHr) || parsedThresholdHr <= 0)) ||
      (parsedAge !== undefined && (isNaN(parsedAge) || parsedAge < 10))
    ) {
      setHrMsg({ type: 'error', text: 'Please enter valid values (age must be at least 10).' })
      return
    }

    if (!resolvedMaxHr && !parsedRestingHr && !parsedThresholdHr) {
      setHrMsg({ type: 'error', text: 'Please enter at least one heart rate value (or your age).' })
      return
    }

    setHrWorking(true)
    setHrMsg(null)
    setFtpEstimate(null)
    setFtpConfirmInput('')
    try {
      // Explicitly persist the new HR values to the backend before estimating FTP.
      await updateCurrentUser(authToken, {
        ...(resolvedMaxHr !== undefined ? { maxHeartRate: resolvedMaxHr } : {}),
        ...(parsedRestingHr !== undefined ? { restingHeartRate: parsedRestingHr } : {}),
        ...(parsedThresholdHr !== undefined ? { thresholdHeartRate: parsedThresholdHr } : {}),
      })
      // Update local profile state with new HR values.
      setUserProfile({
        ...userProfile,
        ...(resolvedMaxHr !== undefined ? { maxHeartRate: resolvedMaxHr } : {}),
        ...(parsedRestingHr !== undefined ? { restingHeartRate: parsedRestingHr } : {}),
        ...(parsedThresholdHr !== undefined ? { thresholdHeartRate: parsedThresholdHr } : {}),
      })
      const result = await estimateFTP(authToken, {
        maxHeartRate: resolvedMaxHr,
        restingHeartRate: parsedRestingHr,
        thresholdHeartRate: parsedThresholdHr,
      })
      if (result.estimatedFTP !== null && result.estimatedFTP !== undefined) {
        setFtpEstimate(result.estimatedFTP)
        setFtpConfirmInput(String(result.estimatedFTP))
        setHrMsg({
          type: 'success',
          text: 'Heart rate values saved. Review your estimated FTP below and confirm to rebuild metrics.',
        })
      } else {
        setHrMsg({
          type: 'success',
          text: 'Heart rate values saved. No ride data available yet to estimate FTP — sync Strava first.',
        })
      }
    } catch {
      setHrMsg({ type: 'error', text: 'Failed to save heart rate values. Please try again.' })
    } finally {
      setHrWorking(false)
    }
  }

  // Step 2: Confirm the FTP and trigger a full metrics recalculation.
  const handleConfirmFTP = async () => {
    if (!authToken) return
    const parsed = parseInt(ftpConfirmInput, 10)
    if (isNaN(parsed) || parsed <= 0) {
      setHrMsg({ type: 'error', text: 'Please enter a valid positive FTP value in watts.' })
      return
    }
    const confirmed = window.confirm(
      '⚠️ Recalculate TSS, ATL, CTL for all rides?\n\n' +
        `This will overwrite all historical training stress values using ${parsed} W as the reference. ` +
        'This operation cannot be reversed.\n\nContinue?'
    )
    if (!confirmed) return

    setHrMsg(null)
    try {
      const result = await updateMetrics({ currentFTP: parsed })
      setFtpEstimate(null)
      setFtpConfirmInput('')
      setMaxHrInput('')
      setRestingHrInput('')
      setThresholdHrInput('')
      setAgeInput('')
      setHrMsg({
        type: 'success',
        text: `Done! Recalculated ${result.updated} rides using FTP ${result.ftpUsed} W.`,
      })
      setTimeout(() => setHrMsg(null), 6000)
    } catch (e) {
      setHrMsg({ type: 'error', text: e instanceof Error ? e.message : 'Metrics recalculation failed. Please try again.' })
    }
  }

  const handleLogout = () => {
    logout()
    if (AUTHELIA_URL) {
      window.location.href = `${AUTHELIA_URL}/logout`
    }
  }

  const providers: { value: AiProvider; label: string; hint: string; placeholder: string }[] = [
    { value: 'openai', label: 'OpenAI', hint: 'GPT-4o mini', placeholder: 'sk-...' },
    { value: 'gemini', label: 'Google Gemini', hint: 'Gemini 2.0 Flash', placeholder: 'AIza...' },
  ]

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Manage your AI provider, account, and integrations</p>
      </div>

      {savedMsg && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-xl px-4 py-3 text-sm">
          {savedMsg}
        </div>
      )}

      {/* AI Provider */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">AI Provider</h2>
        <p className="text-xs text-gray-500 mb-4">
          Choose which server-side model powers your training plan and coach chat.
        </p>

        <div className="grid grid-cols-2 gap-3 mb-4">
          {providers.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setSelectedProvider(p.value)}
              className={`flex flex-col items-start px-4 py-3 rounded-xl border-2 text-left transition-all ${
                selectedProvider === p.value
                  ? 'border-amber-500 bg-amber-50'
                  : 'border-gray-200 hover:border-amber-300'
              }`}
            >
              <span className="font-semibold text-sm text-gray-900">{p.label}</span>
              <span className="text-xs text-gray-500">{p.hint}</span>
            </button>
          ))}
        </div>

        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4 flex gap-2">
          <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-yellow-700">
            Model keys live on the backend now. The browser no longer stores or sends provider API keys.
          </p>
        </div>

        <div className="flex gap-2 flex-wrap">
          <button
            onClick={saveAI}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600"
          >
            <Save size={15} /> Save
          </button>
        </div>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Account</h2>
        <p className="text-xs text-gray-500 mb-4">Signed in as {userProfile?.email ?? 'unknown'}.</p>

        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-700 mb-1">
            <span className="flex items-center gap-1"><User size={13} /> Display Name</span>
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void saveName()}
              placeholder="Your name"
              className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            <button
              onClick={() => void saveName()}
              disabled={!nameInput.trim() || nameInput.trim() === (userProfile?.name ?? '')}
              className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
            >
              <Save size={15} /> Save
            </button>
          </div>
        </div>

        <button
          onClick={handleLogout}
          className="flex items-center gap-1.5 border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50"
        >
          <LogOut size={15} /> Sign Out
        </button>
      </div>

      {/* FTP Management */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">FTP Management</h2>
        <p className="text-xs text-gray-500 mb-4">
          Override your current FTP value and optionally recalculate all historical training-stress metrics
          (TSS, ATL, CTL, TSB) using the new value.
          {userProfile?.currentFTP != null && (
            <span className="ml-1 font-medium text-gray-700">
              Current FTP: <span className="text-purple-700">{userProfile.currentFTP} W</span>
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

        <div className="flex gap-2 mb-4">
          <div className="relative flex-1">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
              <Zap size={14} />
            </span>
            <input
              type="number"
              min={1}
              value={ftpInput}
              onChange={(e) => setFtpInput(e.target.value)}
              placeholder={userProfile?.currentFTP != null ? String(userProfile.currentFTP) : 'e.g. 250'}
              className="w-full border border-gray-300 rounded-lg pl-8 pr-10 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">W</span>
          </div>
          <button
            onClick={() => void saveFTP()}
            disabled={ftpSaving || !ftpInput.trim()}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
          >
            <Save size={15} /> {ftpSaving ? 'Saving…' : 'Save FTP'}
          </button>
        </div>

        <div className="border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500 mb-3">
            <strong className="text-gray-700">Recalculate metrics</strong> — rebuilds TSS, CTL, ATL, and
            TSB for every stored ride using the FTP entered above (or your current FTP if no value is
            entered). FTP estimates derived automatically from ride data are shown in the Athlete
            Progression chart on your dashboard.
          </p>
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-3 flex gap-2">
            <AlertTriangle size={15} className="text-yellow-600 flex-shrink-0 mt-0.5" />
            <p className="text-xs text-yellow-700">
              This operation <strong>cannot be reversed</strong>. All historical training-stress values
              will be overwritten. A confirmation dialog will appear before any data is changed.
            </p>
          </div>
          <button
            onClick={() => void handleRecalculate()}
            disabled={isPipelinePending}
            className="flex items-center gap-1.5 bg-purple-600 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-purple-700 disabled:opacity-50"
          >
            <RefreshCw size={15} className={isPipelinePending ? 'animate-spin' : ''} />
            {isPipelinePending ? 'Recalculating…' : 'Recalculate TSS / ATL / CTL'}
          </button>
        </div>
      </div>

      {/* Heart Rate Settings */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Heart Rate Settings</h2>
        <p className="text-xs text-gray-500 mb-1">
          Max HR, resting HR, and threshold HR are used for HR-based FTP estimation and training zones.
        </p>
        {(userProfile?.maxHeartRate != null || userProfile?.restingHeartRate != null || userProfile?.thresholdHeartRate != null) && (
          <p className="text-xs text-gray-500 mb-4">
            Current:{' '}
            {userProfile.maxHeartRate != null && (
              <span className="font-medium text-gray-700 mr-2">Max HR: <span className="text-red-600">{userProfile.maxHeartRate} bpm</span></span>
            )}
            {userProfile.restingHeartRate != null && (
              <span className="font-medium text-gray-700 mr-2">Resting HR: <span className="text-blue-600">{userProfile.restingHeartRate} bpm</span></span>
            )}
            {userProfile.thresholdHeartRate != null && (
              <span className="font-medium text-gray-700">Threshold HR: <span className="text-orange-600">{userProfile.thresholdHeartRate} bpm</span></span>
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
            <label className="block text-xs font-semibold text-gray-700 mb-1">
              <span className="flex items-center gap-1"><Heart size={13} /> Max Heart Rate (bpm)</span>
            </label>
            <input
              type="number"
              min={1}
              value={maxHrInput}
              onChange={(e) => setMaxHrInput(e.target.value)}
              placeholder={userProfile?.maxHeartRate != null ? String(userProfile.maxHeartRate) : 'e.g. 185'}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            {!maxHrInput && (
              <div className="mt-2">
                <label className="block text-xs text-gray-500 mb-1">
                  Or enter your age to estimate Max HR (220 − age):
                </label>
                <input
                  type="number"
                  min={1}
                  value={ageInput}
                  onChange={(e) => setAgeInput(e.target.value)}
                  placeholder="e.g. 35"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                />
                {ageInput && (() => {
                  const parsedAge = parseInt(ageInput, 10)
                  const est = Number.isFinite(parsedAge) && parsedAge >= 10
                    ? Math.max(100, 220 - parsedAge)
                    : undefined
                  return est !== undefined ? (
                    <p className="text-xs text-amber-700 mt-1">
                      Estimated Max HR: {est} bpm
                    </p>
                  ) : null
                })()}
              </div>
            )}
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">
              <span className="flex items-center gap-1"><Heart size={13} /> Resting Heart Rate (bpm)</span>
            </label>
            <input
              type="number"
              min={1}
              value={restingHrInput}
              onChange={(e) => setRestingHrInput(e.target.value)}
              placeholder={userProfile?.restingHeartRate != null ? String(userProfile.restingHeartRate) : 'e.g. 55 (default: 60)'}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">
              <span className="flex items-center gap-1"><Heart size={13} /> Threshold Heart Rate (bpm)</span>
            </label>
            <input
              type="number"
              min={1}
              value={thresholdHrInput}
              onChange={(e) => setThresholdHrInput(e.target.value)}
              placeholder={userProfile?.thresholdHeartRate != null ? String(userProfile.thresholdHeartRate) : 'e.g. 162 (optional)'}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
            />
            <p className="text-xs text-gray-400 mt-1">
              Lactate threshold HR — the highest HR you can sustain for ~60 min. Typically ~87% of max HR.
            </p>
          </div>
        </div>

        <button
          onClick={() => void handleSaveHR()}
          disabled={hrWorking || (!maxHrInput.trim() && !ageInput.trim() && !restingHrInput.trim() && !thresholdHrInput.trim())}
          className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
        >
          <Heart size={15} className={hrWorking ? 'animate-pulse' : ''} />
          {hrWorking ? 'Saving…' : 'Save & Estimate FTP'}
        </button>

        {/* Step 2: FTP confirmation panel */}
        {ftpEstimate !== null && (
          <div className="mt-4 border border-amber-200 bg-amber-50 rounded-xl p-4 space-y-3">
            <p className="text-sm font-semibold text-gray-800">
              Step 2 — Confirm FTP before rebuilding metrics
            </p>
            <p className="text-xs text-gray-600">
              Based on your ride history, your estimated FTP is{' '}
              <strong className="text-purple-700">{ftpEstimate} W</strong>. You can adjust this value
              before recalculating all training-stress metrics (TSS, ATL, CTL, TSB).
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
                {isPipelinePending ? 'Recalculating…' : 'Confirm & Recalculate'}
              </button>
            </div>
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 flex gap-2">
              <AlertTriangle size={14} className="text-yellow-600 flex-shrink-0 mt-0.5" />
              <p className="text-xs text-yellow-700">
                This will overwrite all historical training-stress values and <strong>cannot be reversed</strong>.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Strava */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Strava Integration</h2>
        <p className="text-xs text-gray-500 mb-4">
          Connect Strava to sync your activities automatically.
        </p>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex gap-2">
          <Server size={16} className="text-blue-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-blue-700">
            OAuth is handled by the backend at <code className="font-mono bg-blue-100 px-1 rounded">{BACKEND_URL}</code>.
            Your Strava credentials are never stored in the browser.
          </p>
        </div>

        <StravaConnect />

        {importProgress.status !== 'idle' && (
          <div className="mt-4 border border-gray-100 rounded-xl p-4 bg-gray-50">
            <p className="text-xs font-semibold text-gray-700 mb-2">Ride history import</p>
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
                      ? `${importProgress.processed} / ${importProgress.total} rides (${pct}%)`
                      : 'Fetching ride list…'}
                  </p>
                </>
              )
            })()}
            {importProgress.status === 'done' && (
              <p className="text-xs text-green-700">
                ✓ {importProgress.processed} ride{importProgress.processed !== 1 ? 's' : ''} imported
                {importProgress.skipped > 0 ? `, ${importProgress.skipped} skipped` : ''}
              </p>
            )}
            {importProgress.status === 'error' && (
              <p className="text-xs text-red-600">Import failed: {importProgress.error || 'unknown error'}</p>
            )}
          </div>
        )}
      </div>

      {/* Danger zone */}
      <div className="bg-white rounded-2xl shadow-sm border border-red-100 p-6">
        <h2 className="text-base font-bold text-red-600 mb-1">Danger Zone</h2>
        <p className="text-xs text-gray-500 mb-4">
          Permanently delete all your data and start over from scratch.
        </p>
        <button
          onClick={handleReset}
          className="flex items-center gap-1.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100"
        >
          <Trash2 size={15} /> Reset All Data
        </button>
      </div>
    </div>
  )
}
