import { useState, useEffect, useRef } from 'react'
import {
  Bike,
  CheckCircle,
  CheckCircle2,
  Dumbbell,
  Link,
  Loader2,
  Target,
  Trophy,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type UserProfile, type RiderAssessment } from '../store/useAppStore'
import IntervalsConnect from '../components/IntervalsConnect'
import StravaConnect from '../components/StravaConnect'
import { analyseStravaActivities, generateTrainingPlan } from '../services/ai'
import { getIntervalsActivities } from '../services/intervals'
import { getStravaActivities } from '../services/strava'
import { saveTrainingPlan, updateCurrentUser } from '../services/user'

const TOTAL_STEPS = 5
const ONBOARDING_STORAGE_KEY = 'ai_trainer_onboarding_progress'

// Only non-sensitive fields are persisted across the OAuth redirect.
// Health metrics (HR, FTP) are intentionally excluded and re-populated
// from the user profile on restore to avoid clear-text health data in storage.
type PersistedProgress = {
  step: number
  trainingGoal: FormData['trainingGoal']
  raceDate: string
  raceDescription: string
  assessmentMethod: FormData['assessmentMethod']
  followsTrainingPlan: boolean
  fitnessLevel: FormData['fitnessLevel']
}

function readOnboardingProgress(): PersistedProgress | null {
  try {
    const raw = sessionStorage.getItem(ONBOARDING_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedProgress) : null
  } catch {
    return null
  }
}

function saveOnboardingProgress(step: number, form: FormData): void {
  try {
    const progress: PersistedProgress = {
      step,
      trainingGoal: form.trainingGoal,
      raceDate: form.raceDate,
      raceDescription: form.raceDescription,
      assessmentMethod: form.assessmentMethod,
      followsTrainingPlan: form.followsTrainingPlan,
      fitnessLevel: form.fitnessLevel,
    }
    sessionStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(progress))
  } catch {
    // sessionStorage may be unavailable; silently ignore
  }
}

function clearOnboardingProgress(): void {
  try {
    sessionStorage.removeItem(ONBOARDING_STORAGE_KEY)
  } catch {
    // ignore
  }
}

type FormData = {
  trainingGoal: UserProfile['trainingGoal']
  raceDate: string
  raceDescription: string
  // intervals.icu first (ai-trainer-ops#20): it is where this audience already
  // keeps its data, and it carries none of Strava's API-terms risk.
  assessmentMethod: 'intervals' | 'strava' | 'manual'
  followsTrainingPlan: boolean
  currentFTP: string
  fitnessLevel: UserProfile['fitnessLevel']
  maxHeartRate: string
}

function normalizeTrainingGoal(goal: unknown): FormData['trainingGoal'] {
  return goal === 'race' ? 'race' : 'general_fitness'
}

export default function OnboardingPage() {
  const {
    userProfile,
    authToken,
    stravaConnection,
    intervalsConnection,
    setUserProfile,
    setTrainingPlan,
    setRiderAssessment,
    setStravaAnalysisComplete,
    setIntervalsAnalysisComplete,
    setOnboarded,
  } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      intervalsConnection: s.intervalsConnection,
      setUserProfile: s.setUserProfile,
      setTrainingPlan: s.setTrainingPlan,
      setRiderAssessment: s.setRiderAssessment,
      setStravaAnalysisComplete: s.setStravaAnalysisComplete,
      setIntervalsAnalysisComplete: s.setIntervalsAnalysisComplete,
      setOnboarded: s.setOnboarded,
    }))
  )

  const defaultAssessmentMethod = (): FormData['assessmentMethod'] => {
    const hasManualMetrics = Boolean(
      userProfile?.currentFTP || userProfile?.maxHeartRate
    )
    if (hasManualMetrics) return 'manual'
    if (intervalsConnection) return 'intervals'
    if (stravaConnection) return 'strava'
    return 'manual'
  }

  const defaultTrainingGoal = (): FormData['trainingGoal'] =>
    userProfile?.trainingGoal === 'race' || userProfile?.raceDate ? 'race' : 'general_fitness'

  const defaultForm = (): FormData => {
    const trainingGoal = defaultTrainingGoal()
    return {
      trainingGoal,
      raceDate: trainingGoal === 'race' ? userProfile?.raceDate ?? '' : '',
      raceDescription: trainingGoal === 'race' ? userProfile?.raceDescription ?? '' : '',
      assessmentMethod: defaultAssessmentMethod(),
      followsTrainingPlan: userProfile?.followsTrainingPlan ?? false,
      currentFTP: userProfile?.currentFTP ? String(userProfile.currentFTP) : '',
      fitnessLevel: userProfile?.fitnessLevel ?? 'intermediate',
      maxHeartRate: userProfile?.maxHeartRate ? String(userProfile.maxHeartRate) : '',
    }
  }

  // Restore progress saved before the Strava OAuth redirect (if any).
  const savedProgress = readOnboardingProgress()
  const [step, setStep] = useState(savedProgress?.step ?? 1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<FormData>(() => {
    const base = defaultForm()
    if (!savedProgress) return base
    const trainingGoal = normalizeTrainingGoal(savedProgress.trainingGoal)
    // Merge persisted non-sensitive fields with health metrics from the user profile.
    return {
      ...base,
      trainingGoal,
      raceDate: trainingGoal === 'race' ? savedProgress.raceDate : '',
      raceDescription: trainingGoal === 'race' ? savedProgress.raceDescription : '',
      assessmentMethod: savedProgress.assessmentMethod,
      followsTrainingPlan: savedProgress.followsTrainingPlan,
      fitnessLevel: savedProgress.fitnessLevel,
    }
  })

  // True only when the component mounted after a Strava OAuth redirect (saved
  // progress had step=3 with assessmentMethod='strava').  Cleared after the
  // auto-advance fires so that manual Back-navigation never re-triggers it.
  const restoredAtStravaStepRef = useRef(
    savedProgress?.step === 4 && savedProgress?.assessmentMethod === 'strava'
  )

  // After the Strava OAuth redirect, auto-trigger generation once
  // stravaConnection becomes available (user already saw step 4 assessment).
  useEffect(() => {
    if (!restoredAtStravaStepRef.current) return
    if (step !== 4 || form.assessmentMethod !== 'strava' || !stravaConnection) return
    restoredAtStravaStepRef.current = false
    void handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stravaConnection, step, form.assessmentMethod])

  // Persist step and form to sessionStorage so the Strava OAuth redirect does not lose progress.
  useEffect(() => {
    saveOnboardingProgress(step, form)
  }, [step, form])

  const update = (key: keyof FormData, value: FormData[keyof FormData]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const selectTrainingGoal = (trainingGoal: FormData['trainingGoal']) =>
    setForm((f) => ({
      ...f,
      trainingGoal,
      ...(trainingGoal === 'general_fitness' ? { raceDate: '', raceDescription: '' } : {}),
    }))

  const canNext = () => {
    if (step === 2 && form.trainingGoal === 'race') return form.raceDate.trim().length > 0
    if (step === 4 && form.assessmentMethod === 'strava') return Boolean(stravaConnection)
    if (step === 4 && form.assessmentMethod === 'intervals') return Boolean(intervalsConnection)
    return true
  }

  const persistMetricsBeforeStravaConnect = async () => {
    saveOnboardingProgress(4, { ...form, assessmentMethod: 'strava' })
    if (!authToken || !userProfile) return

    const resolvedMaxHR: number | undefined = form.maxHeartRate
      ? Number(form.maxHeartRate)
      : undefined

    const updates: Partial<UserProfile> = {
      currentFTP: form.currentFTP ? Number(form.currentFTP) : undefined,
      maxHeartRate: resolvedMaxHR,
    }

    await updateCurrentUser(authToken, updates)
    setUserProfile({
      ...userProfile,
      ...updates,
    })
  }

  const handleGenerate = async () => {
    if (!authToken) return

    setLoading(true)
    setError('')

    let riderAssessment: RiderAssessment | undefined

    // Resolve Max HR: use explicitly entered value.
    const resolvedMaxHR: number | undefined = form.maxHeartRate
      ? Number(form.maxHeartRate)
      : undefined

    const isRacePrep = form.trainingGoal === 'race'
    const profile: UserProfile = {
      // Read from the loaded profile, never from the form: the form used to
      // copy them at mount, before the profile had arrived, and then wrote the
      // empty copy back over the name given at registration
      // (ai-trainer-ops#40). Onboarding asks for neither, so it sends neither.
      // At click time, not render time: the profile may have arrived since
      // this render, and the store must not be handed an emptier one.
      name: useAppStore.getState().userProfile?.name ?? '',
      email: useAppStore.getState().userProfile?.email ?? '',
      bikeType: userProfile?.bikeType ?? 'road',
      trainingGoal: isRacePrep ? 'race' : 'general_fitness',
      raceDate: isRacePrep ? form.raceDate || undefined : undefined,
      raceDescription: isRacePrep ? form.raceDescription || undefined : undefined,
      followsTrainingPlan: form.followsTrainingPlan,
      currentFTP: form.currentFTP ? Number(form.currentFTP) : undefined,
      fitnessLevel: form.fitnessLevel,
      maxHeartRate: resolvedMaxHR,
    }

    try {
      let profileForPlan = profile
      let stravaAnalysisComplete = false
      let intervalsAnalysisComplete = false

      // The same seven-ride analysis whichever service the rides come from; the
      // backend reads `source` to know which activity identity it is looking at.
      const source =
        form.assessmentMethod === 'intervals' && intervalsConnection
          ? 'intervals'
          : form.assessmentMethod === 'strava' && stravaConnection
            ? 'strava'
            : null
      if (source) {
        const activities =
          source === 'intervals'
            ? await getIntervalsActivities(authToken)
            : await getStravaActivities(authToken)
        const recentActivities = activities.slice(0, 7)
        if (recentActivities.length > 0) {
          const analyseResult = await analyseStravaActivities(
            recentActivities,
            authToken,
            resolvedMaxHR,
            profile.currentFTP,
            source,
          )
          riderAssessment = analyseResult.assessment
          profileForPlan = {
            ...profile,
            currentFTP: profile.currentFTP,
            maxHeartRate: profile.maxHeartRate,
          }
          if (source === 'intervals') {
            intervalsAnalysisComplete = true
          } else {
            stravaAnalysisComplete = true
          }
        }
      }

      // The profile first, without `isOnboarded` — name and email are not
      // onboarding's to write (ai-trainer-ops#40).
      await updateCurrentUser(authToken, {
        ...profileForPlan,
        name: undefined,
        email: undefined,
        stravaAnalysisComplete,
        intervalsAnalysisComplete,
      })
      const plan = await generateTrainingPlan(authToken)
      await saveTrainingPlan(authToken, plan)

      // `isOnboarded` last, and only once a plan exists on the server
      // (ai-trainer-ops#41). It used to go out with the profile above, so a
      // failing plan generation — a 402 for a missing AI key is the ordinary
      // case in BYOK-only mode — left the account marked onboarded with no
      // plan. The error was shown here and the local flag stayed false, so it
      // looked survivable; a reload then read `isOnboarded` from the server and
      // landed on the dashboard saying "No session planned for today", with
      // nothing anywhere naming the cause. Failing before this line instead
      // keeps the athlete in onboarding, where the message is.
      await updateCurrentUser(authToken, { isOnboarded: true })

      if (riderAssessment) {
        setRiderAssessment(riderAssessment)
      }
      setStravaAnalysisComplete(stravaAnalysisComplete)
      setIntervalsAnalysisComplete(intervalsAnalysisComplete)
      setUserProfile(profileForPlan)
      setTrainingPlan(plan)
      clearOnboardingProgress()
      setOnboarded(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate plan.')
    } finally {
      setLoading(false)
    }
  }

  const connectedForAnalysis =
    (form.assessmentMethod === 'intervals' && Boolean(intervalsConnection)) ||
    (form.assessmentMethod === 'strava' && Boolean(stravaConnection))

  const handleContinue = () => {
    if (step === 4 && connectedForAnalysis) {
      void handleGenerate()
    } else {
      setStep(step + 1)
    }
  }

  const goals: {
    value: UserProfile['trainingGoal']
    label: string
    desc: string
    Icon: LucideIcon
  }[] = [
    { value: 'race', label: 'Race Prep', desc: 'Be ready for a specific race', Icon: Trophy },
    {
      value: 'general_fitness',
      label: 'General Fitness',
      desc: 'Stay fit and healthy',
      Icon: Dumbbell,
    },
  ]

  const levels: { value: UserProfile['fitnessLevel']; label: string; desc: string }[] = [
    { value: 'beginner', label: 'Beginner', desc: 'Cycling < 1 year' },
    { value: 'intermediate', label: 'Intermediate', desc: '1-3 years experience' },
    { value: 'advanced', label: 'Advanced', desc: '3+ years, racing or structured training' },
  ]

  return (
    <div className="min-h-screen bg-[#0f1116] flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg">
        {/* Header */}
        <div className="px-8 pt-8 pb-4">
          <div className="flex items-center gap-2 mb-6">
            <div className="bg-amber-500 rounded-lg p-1.5">
              <Bike size={22} className="text-white" />
            </div>
            <span className="font-bold text-xl text-gray-900">Train Like a Pro!</span>
          </div>
          <div className="mb-2">
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              <span>Step {step} of {TOTAL_STEPS}</span>
              <span>{Math.round((step / TOTAL_STEPS) * 100)}%</span>
            </div>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all duration-300"
                style={{ width: `${(step / TOTAL_STEPS) * 100}%` }}
              />
            </div>
          </div>
        </div>

        <div className="px-8 pb-8">
          {/* Step 1: Welcome greeting */}
          {step === 1 && (
            <div className="text-center py-4">
              <div className="bg-amber-100 rounded-full w-16 h-16 flex items-center justify-center mx-auto mb-4">
                <Dumbbell size={32} className="text-amber-500" />
              </div>
              <h2 className="text-2xl font-bold text-gray-900 mb-2">
                {/* No waving hand (ai-trainer-ops#51.7) — and this heading
                    already has a 32 px dumbbell directly above it. */}
                Welcome, {userProfile?.name?.trim() || 'athlete'}!
              </h2>
              <p className="text-gray-500 mb-4 text-sm leading-relaxed">
                We want to set up your first <span className="font-semibold text-gray-700">14-day cycling training plan</span>.
                We just need a few quick inputs.
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-800 text-left space-y-1">
                {/* Three ticks that were ✅, which is a green box in a browser
                    with no emoji font (ai-trainer-ops#51.7). `aria-hidden`:
                    read aloud, "white heavy check mark" three times adds
                    nothing to three plain statements. */}
                {[
                  'A few short questions',
                  'Takes about 3 minutes',
                  'You can update details later anytime',
                ].map((promise) => (
                  <p key={promise} className="flex items-start gap-1.5">
                    <CheckCircle2
                      size={14}
                      className="mt-0.5 shrink-0 text-amber-600"
                      aria-hidden="true"
                    />
                    {promise}
                  </p>
                ))}
              </div>
            </div>
          )}

          {/* Step 2: Training goal */}
          {step === 2 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Are you preparing for a race?</h2>
              <p className="text-sm text-gray-500 mb-4">Race prep adds a target date; otherwise your plan starts as general fitness.</p>
              <div className="space-y-2">
                {goals.map((g) => (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => selectTrainingGoal(g.value)}
                    className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-left transition-all ${
                      form.trainingGoal === g.value
                        ? 'border-amber-500 bg-amber-50'
                        : 'border-gray-200 hover:border-amber-300'
                    }`}
                  >
                    {/* `aria-hidden`: the goal's name is right beside it. */}
                    <g.Icon size={24} className="shrink-0 text-amber-500" aria-hidden="true" />
                    <div>
                      <div className="font-semibold text-sm text-gray-900">{g.label}</div>
                      <div className="text-xs text-gray-500">{g.desc}</div>
                    </div>
                    {form.trainingGoal === g.value && (
                      <CheckCircle size={16} className="ml-auto text-amber-500" />
                    )}
                  </button>
                ))}
              </div>
              {form.trainingGoal === 'race' && (
                <div className="mt-4 space-y-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Race Date *</label>
                    <input
                      type="date"
                      value={form.raceDate}
                      onChange={(e) => update('raceDate', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Race Description</label>
                    <input
                      type="text"
                      value={form.raceDescription}
                      onChange={(e) => update('raceDescription', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder="e.g. 80km gran fondo with 2000m climbing"
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Step 3: Training Inputs */}
          {step === 3 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Training Inputs</h2>
              <p className="text-sm text-gray-500 mb-4">
                Tell us about your current fitness — everything is optional and can be updated later.
              </p>
              <div className="space-y-5">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Fitness Level</label>
                  <div className="space-y-2">
                    {levels.map((l) => (
                      <button
                        key={l.value}
                        type="button"
                        onClick={() => update('fitnessLevel', l.value)}
                        className={`w-full flex items-center justify-between px-4 py-3 rounded-lg border-2 transition-all ${
                          form.fitnessLevel === l.value
                            ? 'border-amber-500 bg-amber-50'
                            : 'border-gray-200 hover:border-amber-300'
                        }`}
                      >
                        <div className="text-left">
                          <div className="font-semibold text-sm">{l.label}</div>
                          <div className="text-xs text-gray-500">{l.desc}</div>
                        </div>
                        {form.fitnessLevel === l.value && (
                          <CheckCircle size={16} className="text-amber-500" />
                        )}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Current FTP (watts) <span className="text-gray-400 font-normal">optional</span>
                    </label>
                    <input
                      type="number"
                      value={form.currentFTP}
                      onChange={(e) => update('currentFTP', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder="e.g. 250"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Max Heart Rate (bpm) <span className="text-gray-400 font-normal">optional</span>
                    </label>
                    <input
                      type="number"
                      value={form.maxHeartRate}
                      onChange={(e) => update('maxHeartRate', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder="e.g. 185"
                    />

                  </div>
                </div>
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.followsTrainingPlan}
                    onChange={(e) => update('followsTrainingPlan', e.target.checked)}
                    className="w-4 h-4 rounded accent-amber-500"
                  />
                  <span className="text-sm text-gray-700">I currently follow a structured training plan</span>
                </label>
              </div>
            </div>
          )}

          {/* Step 4: Fitness assessment method */}
          {step === 4 && (
            <div>
              {loading ? (
                <div className="text-center py-10">
                  <Loader2 size={32} className="animate-spin text-amber-500 mx-auto mb-3" />
                  <p className="font-semibold text-gray-900">Analysing rides and generating your plan…</p>
                  <p className="text-sm text-gray-500 mt-1">This takes about 30 seconds.</p>
                </div>
              ) : (
                <>
                  <h2 className="text-xl font-bold text-gray-900 mb-1">How should we assess your fitness?</h2>
                  <p className="text-sm text-gray-500 mb-4">
                    Connect intervals.icu or Strava for automatic analysis of your last 7 rides, or use the parameters you just entered.
                  </p>
                  <div className="space-y-3">
                    <button
                      type="button"
                      onClick={() => update('assessmentMethod', 'intervals')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'intervals'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">Connect intervals.icu</p>
                          <p className="text-xs text-gray-500 mt-1">
                            Recommended. Analyses your last 7 rides with your intervals.icu API key.
                          </p>
                        </div>
                        {form.assessmentMethod === 'intervals' && (
                          <CheckCircle size={16} className="text-amber-500 shrink-0" />
                        )}
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => update('assessmentMethod', 'strava')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'strava'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">Connect with Strava</p>
                          <p className="text-xs text-gray-500 mt-1">
                            Analyses your last 7 rides to estimate FTP and training zones.
                          </p>
                        </div>
                        {form.assessmentMethod === 'strava' && (
                          <CheckCircle size={16} className="text-amber-500 shrink-0" />
                        )}
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => update('assessmentMethod', 'manual')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'manual'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">Use parameters I entered</p>
                          <p className="text-xs text-gray-500 mt-1">
                            We&apos;ll use the FTP and heart-rate values from the previous step.
                          </p>
                        </div>
                        {form.assessmentMethod === 'manual' && (
                          <CheckCircle size={16} className="text-amber-500 shrink-0" />
                        )}
                      </div>
                    </button>
                  </div>
                  {form.assessmentMethod === 'intervals' && (
                    <div className="mt-4 space-y-3">
                      <IntervalsConnect />
                      {!intervalsConnection && (
                        <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                          <Link size={14} className="shrink-0" />
                          Connect intervals.icu to continue with automatic activity analysis.
                        </div>
                      )}
                    </div>
                  )}
                  {form.assessmentMethod === 'strava' && (
                    <div className="mt-4 space-y-3">
                      <StravaConnect onBeforeConnect={persistMetricsBeforeStravaConnect} />
                      {!stravaConnection && (
                        <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                          <Link size={14} className="shrink-0" />
                          Connect Strava to continue with automatic activity analysis.
                        </div>
                      )}
                    </div>
                  )}
                  {error && (
                    <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mt-4">
                      {error}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Step 5: Summary */}
          {step === 5 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Ready to Go!</h2>
              <p className="text-sm text-gray-500 mb-4">
                Review your inputs and generate your first 14-day plan.
              </p>

              <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm mb-4">
                {(() => {
                  const displayMaxHR = form.maxHeartRate ? `${form.maxHeartRate} bpm` : null
                  return (
                    [
                      ['Name', userProfile?.name ?? ''],
                      ['Email', userProfile?.email ?? ''],
                      ['Goal', form.trainingGoal.replace('_', ' ')],
                      ['Assessment', form.assessmentMethod === 'intervals' ? 'intervals.icu (last 7 rides)' : form.assessmentMethod === 'strava' ? 'Strava (last 7 rides)' : 'Manual'],
                      ...(form.raceDate ? [['Race Date', form.raceDate]] : []),
                      ['Fitness Level', form.fitnessLevel],
                      ...(form.currentFTP ? [['FTP', `${form.currentFTP}W`]] : []),
                      ...(displayMaxHR ? [['Max HR', displayMaxHR]] : []),
                    ] as [string, string][]
                  ).map(([label, value]) => (
                    <div key={label} className="flex justify-between">
                      <span className="text-gray-500">{label}</span>
                      <span className="font-medium text-gray-900 capitalize">{value}</span>
                    </div>
                  ))
                })()}
              </div>

              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">
                  {error}
                </div>
              )}

              <button
                onClick={handleGenerate}
                disabled={loading}
                className="w-full bg-amber-500 text-white rounded-xl py-3 font-semibold flex items-center justify-center gap-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {loading ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    Generating your plan...
                  </>
                ) : (
                  <>
                    <Target size={18} />
                    Generate My 14-Day Training Plan
                  </>
                )}
              </button>
            </div>
          )}

          {/* Navigation */}
          {step < TOTAL_STEPS && !(step === 4 && loading) && (
            <div className="flex gap-3 mt-8">
              {step > 1 && (
                <button
                  onClick={() => setStep(step - 1)}
                  className="flex-1 border border-gray-300 text-gray-700 rounded-xl py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors"
                >
                  Back
                </button>
              )}
              <button
                onClick={handleContinue}
                disabled={!canNext()}
                className="flex-1 bg-amber-500 text-white rounded-xl py-2.5 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {step === 4 && connectedForAnalysis
                  ? 'Analyse & Generate Plan'
                  : 'Continue'}
              </button>
            </div>
          )}
          {step === TOTAL_STEPS && step > 1 && !loading && (
            <button
              onClick={() => setStep(step - 1)}
              className="w-full mt-2 text-sm text-gray-500 hover:text-gray-700 py-1"
            >
              ← Back
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
