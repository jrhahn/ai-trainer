import { useState, useEffect } from 'react'
import { Bike, Target, Loader2, CheckCircle, Dumbbell, Link } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type UserProfile, type RiderAssessment } from '../store/useAppStore'
import StravaConnect from '../components/StravaConnect'
import { analyseStravaActivities, generateTrainingPlan } from '../services/ai'
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

/** Estimate maximum heart rate from age using the 220 − age formula.
 *  Age is clamped to a minimum of 10 to avoid physiologically unrealistic
 *  values for very young inputs.  Returns undefined when age is not a valid
 *  positive number.
 */
function estimateMaxHRFromAge(age: number): number | undefined {
  if (!Number.isFinite(age) || age < 10) return undefined
  return Math.max(100, 220 - age)
}

type FormData = {
  name: string
  email: string
  trainingGoal: UserProfile['trainingGoal']
  raceDate: string
  raceDescription: string
  assessmentMethod: 'strava' | 'manual'
  followsTrainingPlan: boolean
  currentFTP: string
  fitnessLevel: UserProfile['fitnessLevel']
  restingHeartRate: string
  maxHeartRate: string
  age: string
}

export default function OnboardingPage() {
  const {
    userProfile,
    authToken,
    stravaConnection,
    setUserProfile,
    setTrainingPlan,
    setRiderAssessment,
    setStravaAnalysisComplete,
    setOnboarded,
  } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      setUserProfile: s.setUserProfile,
      setTrainingPlan: s.setTrainingPlan,
      setRiderAssessment: s.setRiderAssessment,
      setStravaAnalysisComplete: s.setStravaAnalysisComplete,
      setOnboarded: s.setOnboarded,
    }))
  )

  const defaultAssessmentMethod = (): FormData['assessmentMethod'] => {
    const hasManualMetrics = Boolean(
      userProfile?.currentFTP || userProfile?.maxHeartRate || userProfile?.restingHeartRate
    )
    if (hasManualMetrics || !stravaConnection) return 'manual'
    return 'strava'
  }

  const defaultForm = (): FormData => ({
    name: userProfile?.name ?? '',
    email: userProfile?.email ?? '',
    trainingGoal: userProfile?.trainingGoal ?? 'general_fitness',
    raceDate: userProfile?.raceDate ?? '',
    raceDescription: userProfile?.raceDescription ?? '',
    assessmentMethod: defaultAssessmentMethod(),
    followsTrainingPlan: userProfile?.followsTrainingPlan ?? false,
    currentFTP: userProfile?.currentFTP ? String(userProfile.currentFTP) : '',
    fitnessLevel: userProfile?.fitnessLevel ?? 'intermediate',
    restingHeartRate: userProfile?.restingHeartRate ? String(userProfile.restingHeartRate) : '',
    maxHeartRate: userProfile?.maxHeartRate ? String(userProfile.maxHeartRate) : '',
    age: '',
  })

  // Restore progress saved before the Strava OAuth redirect (if any).
  const savedProgress = readOnboardingProgress()
  const [step, setStep] = useState(savedProgress?.step ?? 1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<FormData>(() => {
    const base = defaultForm()
    if (!savedProgress) return base
    // Merge persisted non-sensitive fields with health metrics from the user profile.
    return {
      ...base,
      trainingGoal: savedProgress.trainingGoal,
      raceDate: savedProgress.raceDate,
      raceDescription: savedProgress.raceDescription,
      assessmentMethod: savedProgress.assessmentMethod,
      followsTrainingPlan: savedProgress.followsTrainingPlan,
      fitnessLevel: savedProgress.fitnessLevel,
    }
  })

  // Persist step and form to sessionStorage so the Strava OAuth redirect does not lose progress.
  useEffect(() => {
    saveOnboardingProgress(step, form)
  }, [step, form])

  const update = (key: keyof FormData, value: FormData[keyof FormData]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const canNext = () => {
    if (step === 2 && form.trainingGoal === 'race') return form.raceDate.trim().length > 0
    if (step === 3 && form.assessmentMethod === 'strava') return Boolean(stravaConnection)
    return true
  }

  const handleGenerate = async () => {
    if (!authToken) return

    setLoading(true)
    setError('')

    // Threshold HR is typically ~87% of max HR for trained cyclists.
    // Must stay in sync with _LTHR_RATIO in backend/services/ai_service.py.
    const THRESHOLD_HR_TO_MAX_HR_RATIO = 0.87
    let riderAssessment: RiderAssessment | undefined

    // Resolve Max HR: use explicitly entered value, or estimate from age (220 − age).
    const resolvedMaxHR: number | undefined = form.maxHeartRate
      ? Number(form.maxHeartRate)
      : form.age
      ? estimateMaxHRFromAge(Number(form.age))
      : undefined

    // Default resting HR to 60 when not provided.
    const resolvedRestingHR: number = form.restingHeartRate ? Number(form.restingHeartRate) : 60

    const profile: UserProfile = {
      name: form.name,
      email: form.email,
      bikeType: userProfile?.bikeType ?? 'road',
      trainingGoal: form.trainingGoal,
      raceDate: form.raceDate || undefined,
      raceDescription: form.raceDescription || undefined,
      followsTrainingPlan: form.followsTrainingPlan,
      currentFTP: form.currentFTP ? Number(form.currentFTP) : undefined,
      fitnessLevel: form.fitnessLevel,
      restingHeartRate: resolvedRestingHR,
      maxHeartRate: resolvedMaxHR,
    }

    try {
      let profileForPlan = profile
      let stravaAnalysisComplete = false

      if (form.assessmentMethod === 'strava' && stravaConnection) {
        const activities = await getStravaActivities(authToken)
        const recentActivities = activities.slice(0, 7)
        if (recentActivities.length > 0) {
          const analyseResult = await analyseStravaActivities(
            recentActivities,
            authToken,
            resolvedMaxHR,
          )
          riderAssessment = analyseResult.assessment
          profileForPlan = {
            ...profile,
            currentFTP: profile.currentFTP ?? riderAssessment.estimatedFTP,
            maxHeartRate: profile.maxHeartRate ?? (riderAssessment.estimatedThresholdHR
              ? Math.round(riderAssessment.estimatedThresholdHR / THRESHOLD_HR_TO_MAX_HR_RATIO)
              : undefined),
          }
          stravaAnalysisComplete = true
        }
      }

      await updateCurrentUser(authToken, {
        ...profileForPlan,
        isOnboarded: true,
        stravaAnalysisComplete,
      })
      const plan = await generateTrainingPlan(authToken)
      await saveTrainingPlan(authToken, plan)
      if (riderAssessment) {
        setRiderAssessment(riderAssessment)
      }
      setStravaAnalysisComplete(stravaAnalysisComplete)
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

  const goals: { value: UserProfile['trainingGoal']; label: string; desc: string; emoji: string }[] = [
    { value: 'race', label: 'Race Prep', desc: 'Be ready for a specific race', emoji: '🏆' },
    { value: 'ftp_improvement', label: 'FTP Improvement', desc: 'Build sustained power', emoji: '⚡' },
    { value: 'general_fitness', label: 'General Fitness', desc: 'Stay fit and healthy', emoji: '💪' },
    { value: 'weight_loss', label: 'Weight Loss', desc: 'Burn calories and slim down', emoji: '🔥' },
  ]

  const levels: { value: UserProfile['fitnessLevel']; label: string; desc: string }[] = [
    { value: 'beginner', label: 'Beginner', desc: 'Cycling < 1 year' },
    { value: 'intermediate', label: 'Intermediate', desc: '1-3 years experience' },
    { value: 'advanced', label: 'Advanced', desc: '3+ years, racing or structured training' },
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center p-4">
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
                Welcome, {form.name || 'athlete'}! 👋
              </h2>
              <p className="text-gray-500 mb-4 text-sm leading-relaxed">
                We want to set up your first <span className="font-semibold text-gray-700">14-day cycling training plan</span>.
                We just need a few quick inputs.
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-800 text-left space-y-1">
                <p>✅ A few short questions</p>
                <p>✅ Takes about 3 minutes</p>
                <p>✅ You can update details later anytime</p>
              </div>
            </div>
          )}

          {/* Step 2: Training goal */}
          {step === 2 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">What&apos;s your goal?</h2>
              <p className="text-sm text-gray-500 mb-4">This shapes your first training plan.</p>
              <div className="space-y-2">
                {goals.map((g) => (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => update('trainingGoal', g.value)}
                    className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-left transition-all ${
                      form.trainingGoal === g.value
                        ? 'border-amber-500 bg-amber-50'
                        : 'border-gray-200 hover:border-amber-300'
                    }`}
                  >
                    <span className="text-2xl">{g.emoji}</span>
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

          {/* Step 3: Strava or manual assessment */}
          {step === 3 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">How should we assess your fitness?</h2>
              <p className="text-sm text-gray-500 mb-4">
                Connect Strava for automatic analysis of your last 7 rides, or enter parameters manually.
              </p>
              <div className="space-y-3">
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
                        Works best when rides include both power and heart rate.
                      </p>
                    </div>
                    {form.assessmentMethod === 'strava' && (
                      <CheckCircle size={16} className="text-amber-500 flex-shrink-0" />
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
                      <p className="font-semibold text-sm text-gray-900">Enter fitness parameters manually</p>
                      <p className="text-xs text-gray-500 mt-1">
                        Add FTP and heart-rate details now. Everything is optional and can be updated later.
                      </p>
                    </div>
                    {form.assessmentMethod === 'manual' && (
                      <CheckCircle size={16} className="text-amber-500 flex-shrink-0" />
                    )}
                  </div>
                </button>
              </div>
              {form.assessmentMethod === 'strava' && (
                <div className="mt-4 space-y-4">
                  {/* HR inputs collected BEFORE Strava connection so they're
                      available for the FTP estimation that runs during analysis. */}
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Max Heart Rate (bpm)
                      </label>
                      <input
                        type="number"
                        value={form.maxHeartRate}
                        onChange={(e) => update('maxHeartRate', e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                        placeholder="e.g. 185"
                      />
                      {!form.maxHeartRate && (
                        <div className="mt-2">
                          <label className="block text-xs text-gray-500 mb-1">
                            Or enter your age — we&apos;ll estimate Max HR as 220 − age.
                          </label>
                          <input
                            type="number"
                            value={form.age}
                            onChange={(e) => update('age', e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                            placeholder="e.g. 35"
                          />
                          {form.age && estimateMaxHRFromAge(Number(form.age)) !== undefined && (
                            <p className="text-xs text-amber-700 mt-1">
                              Estimated Max HR: {estimateMaxHRFromAge(Number(form.age))} bpm
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Resting Heart Rate (bpm){' '}
                        <span className="text-gray-400 font-normal">default 60 if left blank</span>
                      </label>
                      <input
                        type="number"
                        value={form.restingHeartRate}
                        onChange={(e) => update('restingHeartRate', e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                        placeholder="e.g. 55"
                      />
                    </div>
                  </div>
                  <StravaConnect />
                  {!stravaConnection && (
                    <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                      <Link size={14} className="flex-shrink-0" />
                      Connect Strava to continue with automatic ride analysis.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 4: Fitness and availability */}
          {step === 4 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Training Inputs</h2>
              <p className="text-sm text-gray-500 mb-4">Helps calibrate workout intensity and schedule.</p>
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
                {form.assessmentMethod === 'manual' && (
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
                        Resting Heart Rate (bpm){' '}
                        <span className="text-gray-400 font-normal">default 60 if left blank</span>
                      </label>
                      <input
                        type="number"
                        value={form.restingHeartRate}
                        onChange={(e) => update('restingHeartRate', e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                        placeholder="e.g. 55 (default: 60)"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Max Heart Rate (bpm)
                      </label>
                      <input
                        type="number"
                        value={form.maxHeartRate}
                        onChange={(e) => update('maxHeartRate', e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                        placeholder="e.g. 185"
                      />
                      {!form.maxHeartRate && (
                        <div className="mt-2">
                          <label className="block text-xs text-gray-500 mb-1">
                            Or enter your age — we&apos;ll estimate Max HR as 220 − age.
                          </label>
                          <input
                            type="number"
                            value={form.age}
                            onChange={(e) => update('age', e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                            placeholder="e.g. 35"
                          />
                          {form.age && estimateMaxHRFromAge(Number(form.age)) !== undefined && (
                            <p className="text-xs text-amber-700 mt-1">
                              Estimated Max HR: {estimateMaxHRFromAge(Number(form.age))} bpm
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {form.assessmentMethod === 'strava' && (
                  <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-xs text-blue-700">
                    We&apos;ll download and analyse your last 7 rides — including detailed power, HR, cadence, and speed data — to estimate your FTP and training zones.
                  </div>
                )}
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

          {/* Step 5: Summary */}
          {step === 5 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Ready to Go!</h2>
              <p className="text-sm text-gray-500 mb-4">
                We&apos;ll generate your first 14-day plan{form.assessmentMethod === 'strava' ? ' after ride analysis' : ''}.
              </p>

              <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm mb-4">
                {(() => {
                  const estimatedHR = form.age ? estimateMaxHRFromAge(Number(form.age)) : undefined
                  const displayMaxHR = form.maxHeartRate
                    ? `${form.maxHeartRate} bpm`
                    : estimatedHR !== undefined
                    ? `${estimatedHR} bpm (estimated from age)`
                    : null
                  const displayRestingHR = form.restingHeartRate
                    ? `${form.restingHeartRate} bpm`
                    : '60 bpm (default)'
                  return (
                    [
                      ['Name', form.name],
                      ['Email', form.email],
                      ['Goal', form.trainingGoal.replace('_', ' ')],
                      ['Assessment', form.assessmentMethod === 'strava' ? 'Strava (last 7 rides)' : 'Manual'],
                      ...(form.raceDate ? [['Race Date', form.raceDate]] : []),
                      ['Fitness Level', form.fitnessLevel],
                      ...(form.currentFTP ? [['FTP', `${form.currentFTP}W`]] : []),
                      ['Resting HR', displayRestingHR],
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
                    {form.assessmentMethod === 'strava' ? 'Downloading rides and generating plan...' : 'Generating your plan...'}
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
          {step < TOTAL_STEPS && (
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
                onClick={() => setStep(step + 1)}
                disabled={!canNext()}
                className="flex-1 bg-amber-500 text-white rounded-xl py-2.5 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                Continue
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
