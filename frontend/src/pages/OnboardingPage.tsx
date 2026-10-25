import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Bike } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type UserProfile } from '../store/useAppStore'
import { setPlan } from '../queries/plan'
import { clearOnboardingProgress } from '../lib/onboardingStorage'
import LegalLinks from '../components/LegalLinks'
import { DaysStep, GoalStep, SportsStep } from '../components/onboarding/QuestionSteps'
import AssessmentStep from '../components/onboarding/AssessmentStep'
import KeyStep from '../components/onboarding/KeyStep'
import SummaryStep from '../components/onboarding/SummaryStep'
import { useAiKeyStep } from '../hooks/useAiKeyStep'
import { generateFirstPlan } from '../services/onboarding'
import { updateCurrentUser } from '../services/user'
import { useT } from '../i18n'
import {
  QUESTION_STEPS,
  STEP_AI_KEY,
  STEP_ASSESSMENT,
  STEP_SUMMARY,
  initialForm,
  readOnboardingProgress,
  saveOnboardingProgress,
  stepsFor,
  type OnboardingForm,
} from '../onboarding/form'

export default function OnboardingPage() {
  const {
    userProfile,
    authToken,
    stravaConnection,
    intervalsConnection,
    setUserProfile,
    setRiderAssessment,
    setStravaAnalysisComplete,
    setIntervalsAnalysisComplete,
    setOnboarded,
    setChatHistory,
  } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
      intervalsConnection: s.intervalsConnection,
      setUserProfile: s.setUserProfile,
      setRiderAssessment: s.setRiderAssessment,
      setStravaAnalysisComplete: s.setStravaAnalysisComplete,
      setIntervalsAnalysisComplete: s.setIntervalsAnalysisComplete,
      setOnboarded: s.setOnboarded,
      setChatHistory: s.setChatHistory,
    }))
  )
  const t = useT()

  // Restore progress saved before the Strava OAuth redirect (if any).
  const savedProgress = readOnboardingProgress()
  const [step, setStep] = useState(savedProgress?.step ?? 1)
  const aiKey = useAiKeyStep(authToken, () => setStep(STEP_SUMMARY))
  const { keyRequired, keyStatusKnown } = aiKey
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<OnboardingForm>(() =>
    initialForm(userProfile, { strava: stravaConnection, intervals: intervalsConnection }, savedProgress),
  )

  // Back from the Strava OAuth redirect, the form already says 'strava'.
  const offerStrava = Boolean(stravaConnection) || form.assessmentMethod === 'strava'

  // True only when the component mounted after a Strava OAuth redirect (saved
  // progress had step=3 with assessmentMethod='strava').  Cleared after the
  // auto-advance fires so that manual Back-navigation never re-triggers it.
  const restoredAtStravaStepRef = useRef(
    savedProgress?.step === 4 && savedProgress?.assessmentMethod === 'strava'
  )

  // Persist step and form to sessionStorage so the Strava OAuth redirect does not lose progress.
  useEffect(() => {
    saveOnboardingProgress(step, form)
  }, [step, form])

  const update = (key: keyof OnboardingForm, value: OnboardingForm[keyof OnboardingForm]) =>
    setForm((f) => ({ ...f, [key]: value }))

  // An analysis error belongs to the way it came from: after "I'll enter
  // sessions myself" the summary still said intervals.icu had refused the key
  // (ai-trainer-ops#212).
  const selectMethod = (method: OnboardingForm['assessmentMethod']) => {
    setError('')
    update('assessmentMethod', method)
  }

  const selectTrainingGoal = (trainingGoal: OnboardingForm['trainingGoal']) =>
    setForm((f) => ({
      ...f,
      trainingGoal,
      ...(trainingGoal === 'general_fitness' ? { raceDate: '', raceDescription: '' } : {}),
    }))

  const canNext = () => {
    if (step === 1) return form.sports.length > 0
    if (step === 3) return form.trainingDays.length > 0
    if (step === 2 && form.trainingGoal === 'race') return form.raceDate.trim().length > 0
    if (step === STEP_ASSESSMENT && form.assessmentMethod === 'strava')
      return Boolean(stravaConnection)
    if (step === STEP_ASSESSMENT && form.assessmentMethod === 'intervals')
      return Boolean(intervalsConnection)
    // The key step advances through its own Save button, which is what writes
    // the key. A Continue that moved on without saving would hand the athlete
    // the same 402 one screen later.
    if (step === STEP_AI_KEY) return false
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
      sports: form.sports,
      trainingDays: form.trainingDays,
      // No longer asked (ai-trainer-ops#43): it changed nothing the athlete could see.
      followsTrainingPlan: userProfile?.followsTrainingPlan ?? false,
      currentFTP: form.currentFTP ? Number(form.currentFTP) : undefined,
      fitnessLevel: form.fitnessLevel,
      maxHeartRate: form.maxHeartRate ? Number(form.maxHeartRate) : undefined,
    }

    // The same seven-ride analysis whichever service the rides come from.
    const source =
      form.assessmentMethod === 'intervals' && intervalsConnection
        ? 'intervals'
        : form.assessmentMethod === 'strava' && stravaConnection
          ? 'strava'
          : null

    try {
      const first = await generateFirstPlan(authToken, profile, source)
      if (!first) {
        // Onboarded already: nothing was written, and the app loads the account.
        await useAppStore.getState().loadUserData()
        return
      }
      if (first.chatHistory) setChatHistory(first.chatHistory)
      if (first.riderAssessment) {
        setRiderAssessment(first.riderAssessment)
      }
      setStravaAnalysisComplete(first.stravaAnalysisComplete)
      setIntervalsAnalysisComplete(first.intervalsAnalysisComplete)
      setUserProfile(profile)
      setPlan(first.plan)
      clearOnboardingProgress()
      setOnboarded(true)
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to generate plan.'
      setError(message)
      // The safety net under the probe. If `fetchAIKeyStatus` failed, or the
      // deployment changed mode mid-session, the first *proof* that a key is
      // needed is this 402 — and leaving the athlete on a summary page with a
      // message naming a Settings screen they cannot reach is the whole of
      // ai-trainer-ops#41. Matched on the backend's own wording, which is
      // pinned by a test on both sides.
      // A spent allowance is a 402 too, and its message names Settings, which
      // onboarding cannot reach. An own key is the one way on (ai-trainer-ops#95).
      const keyMissing = /api key configured/i.test(message)
      if (keyMissing || (e as { status?: number } | null)?.status === 402) {
        // Reworded for where they are about to land. The backend's message says
        // "add your key in Settings → AI Provider", which is right when it is
        // read on the summary and wrong when it is read above the very form it
        // is asking for.
        setError('')
        aiKey.requireKey(
          keyMissing
            ? 'Your coach needs a model to write a plan. Add your Gemini key below and we will carry on from here.'
            : 'The free coaching on this server is used up for now. Add your own Gemini key below and we will carry on from here.'
        )
        setStep(STEP_AI_KEY)
      }
    } finally {
      setLoading(false)
    }
  }

  // After the Strava OAuth redirect, auto-trigger generation once
  // stravaConnection becomes available (user already saw step 4 assessment).
  // An effect event, so it runs the handler of the render it fires in, with
  // the form and the connections as they are then (ai-trainer-ops#274: it
  // used to leave `handleGenerate` out of its deps under an eslint-disable).
  const generateOnStravaReturn = useEffectEvent(() => void handleGenerate())
  useEffect(() => {
    if (!restoredAtStravaStepRef.current) return
    if (step !== STEP_ASSESSMENT || form.assessmentMethod !== 'strava' || !stravaConnection) return
    restoredAtStravaStepRef.current = false
    // Starting the generation is what this effect is for: the athlete asked
    // for it before the Strava redirect, and its first setState is the spinner.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    generateOnStravaReturn()
  }, [stravaConnection, step, form.assessmentMethod])

  const connectedForAnalysis =
    (form.assessmentMethod === 'intervals' && Boolean(intervalsConnection)) ||
    (form.assessmentMethod === 'strava' && Boolean(stravaConnection))

  // `|| step === STEP_AI_KEY`: the key step counts as part of the order
  // whenever the athlete is standing on it, even before the probe has answered.
  // `step` is restored from `sessionStorage`, so a reload during the key step
  // lands here with `keyRequired` still false for a moment — and without this
  // the step was not in the list, so the header read "Step 1 of 5" over the key
  // form and Back had nowhere to go. Raised in review; my `position` clamp
  // below had only straightened the label.
  const steps = stepsFor(keyRequired || step === STEP_AI_KEY)
  // Still clamped, for a step id that is in neither list — a session saved by a
  // build that numbered things differently.
  const position = steps.indexOf(step) >= 0 ? steps.indexOf(step) + 1 : 1
  const atLastStep = step === STEP_SUMMARY
  const question = QUESTION_STEPS.indexOf(step) + 1

  const goToStep = (offset: number) => {
    const next = steps[position - 1 + offset]
    if (next !== undefined) setStep(next)
  }

  // A resumed session that no longer needs a key does not stay on the key step.
  //
  // Reachable two ways: a hosted athlete whose `sessionStorage` holds a step 6
  // from a 402 reroute on a deployment that has since been given a global key,
  // and an athlete who added a key in another tab. Without this they are shown
  // a form for a credential they do not need, with Continue disabled and Back
  // leading nowhere — Save was the only way out. Raised in review. Moved on
  // while rendering, not in an effect (ai-trainer-ops#286).
  if (keyStatusKnown && !keyRequired && step === STEP_AI_KEY) {
    setStep(STEP_SUMMARY)
  }

  const handleContinue = () => {
    // A connected athlete generates straight from the assessment step — but not
    // past the key step. Generating here with no usable key is the 402 that
    // ai-trainer-ops#41 is about, and it would skip the one screen that can fix
    // it.
    // `keyStatusKnown` closes the race the review noted as minor: until the
    // probe answers, `keyRequired` is false, so a quick Strava-connected
    // athlete could still take the shortcut and collect a 402. The fallback
    // recovers from it, but waiting for one request is cheaper than a detour
    // through a failed plan generation.
    if (step === STEP_ASSESSMENT && connectedForAnalysis && keyStatusKnown && !keyRequired) {
      void handleGenerate()
    } else {
      goToStep(1)
    }
  }

  const athleteName = userProfile?.name?.trim()

  return (
    <div className="min-h-screen bg-[#0f1116] flex flex-col items-center justify-center gap-4 p-4">
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
            {/* The questions only, as the welcome text promises four (the
                owner's (a) in ai-trainer-ops#223): "Frage 1 von 4" beside
                "Wir haben 4 kurze Fragen", not "Schritt 1 von 5". The key step
                and the summary carry no number, and the bar is full on them. */}
            <p className="mb-1 min-h-4 text-xs text-gray-400">
              {question > 0 &&
                t('Question {position} of {total}', { position: question, total: QUESTION_STEPS.length })}
            </p>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all duration-300"
                style={{ width: `${question > 0 ? (question / QUESTION_STEPS.length) * 100 : 100}%` }}
              />
            </div>
          </div>
        </div>

        <div className="px-8 pb-8">
          {step === 1 && <SportsStep athleteName={athleteName} form={form} update={update} />}
          {step === 2 && <GoalStep form={form} update={update} selectTrainingGoal={selectTrainingGoal} />}
          {step === 3 && <DaysStep form={form} update={update} />}
          {step === STEP_ASSESSMENT && (
            <AssessmentStep
              form={form}
              loading={loading}
              error={error}
              offerStrava={offerStrava}
              stravaConnected={Boolean(stravaConnection)}
              intervalsConnected={Boolean(intervalsConnection)}
              selectMethod={selectMethod}
              clearError={() => setError('')}
              onBeforeStravaConnect={persistMetricsBeforeStravaConnect}
            />
          )}
          {step === STEP_AI_KEY && <KeyStep aiKey={aiKey} />}
          {step === STEP_SUMMARY && (
            <SummaryStep form={form} error={error} loading={loading} onGenerate={() => void handleGenerate()} />
          )}

          {/* Navigation */}
          {!atLastStep && !(step === STEP_ASSESSMENT && loading) && (
            <div className="flex gap-3 mt-8">
              {position > 1 && (
                <button
                  onClick={() => goToStep(-1)}
                  className="flex-1 border border-gray-300 text-gray-700 rounded-xl py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors"
                >
                  {t('Back')}
                </button>
              )}
              <button
                onClick={handleContinue}
                disabled={!canNext()}
                className="flex-1 bg-amber-500 text-white rounded-xl py-2.5 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {step === STEP_ASSESSMENT && connectedForAnalysis && keyStatusKnown && !keyRequired
                  ? t('Analyse & Generate Plan')
                  : t('Continue')}
              </button>
            </div>
          )}
          {atLastStep && position > 1 && !loading && (
            <button
              onClick={() => goToStep(-1)}
              className="w-full mt-2 text-sm text-gray-500 hover:text-gray-700 py-1"
            >
              ← {t('Back')}
            </button>
          )}
        </div>
      </div>
      <LegalLinks />
    </div>
  )
}
