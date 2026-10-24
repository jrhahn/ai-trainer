import { useState, useEffect, useRef } from 'react'
import {
  AlertTriangle,
  Bike,
  CheckCircle,
  Dumbbell,
  Link,
  Loader2,
  Target,
  Trophy,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type UserProfile, type RiderAssessment, type Sport, type Weekday } from '../store/useAppStore'
import { ONBOARDING_STORAGE_KEY, clearOnboardingProgress } from '../lib/onboardingStorage'
import IntervalsConnect from '../components/IntervalsConnect'
import StravaConnect from '../components/StravaConnect'
import LegalLinks from '../components/LegalLinks'
import { analyseStravaActivities, generateTrainingPlan } from '../services/ai'
import { getIntervalsActivities } from '../services/intervals'
import { getStravaActivities } from '../services/strava'
import {
  fetchAIKeyStatus,
  fetchChatHistory,
  fetchCurrentUser,
  saveAIKey,
  saveTrainingPlan,
  testAIKey,
  updateCurrentUser,
} from '../services/user'
import { useT } from '../i18n'

/** The assessment step, from which a connected athlete generates directly. */
const STEP_ASSESSMENT = 4
/** "Ready to Go!" — the summary, and the only step that generates a plan. */
const STEP_SUMMARY = 5
/**
 * Ask for a Gemini key (ai-trainer-ops#41). Numbered *after* the summary even
 * though it is shown *before* it.
 *
 * The id is persisted in `sessionStorage` so the Strava OAuth redirect does not
 * lose a half-finished onboarding. Renumbering the summary to 6 to make room
 * would send every session saved before this release to whichever screen now
 * holds its old number — in this case the key step, which a hosted athlete must
 * never see. Ids are internal; the number on screen comes from the order below.
 */
const STEP_AI_KEY = 6

/** The steps in the order they are shown, which depends on the deployment.
 *
 * In BYOK-only mode the key step sits between the assessment and the summary:
 * after the questions, so nothing is asked for before the athlete knows what
 * they are getting, and before the summary, so "Generate" is never a button
 * that cannot work. On a hosted deployment it is not in the list at all.
 */
function stepsFor(keyRequired: boolean): number[] {
  return keyRequired
    ? [1, 2, 3, STEP_ASSESSMENT, STEP_AI_KEY, STEP_SUMMARY]
    : [1, 2, 3, STEP_ASSESSMENT, STEP_SUMMARY]
}
// Only non-sensitive fields are persisted across the OAuth redirect.
// Health metrics (HR, FTP) are intentionally excluded and re-populated
// from the user profile on restore to avoid clear-text health data in storage.
type PersistedProgress = {
  step: number
  trainingGoal: FormData['trainingGoal']
  raceDate: string
  raceDescription: string
  assessmentMethod: FormData['assessmentMethod']
  fitnessLevel: FormData['fitnessLevel']
  sports?: Sport[]
  trainingDays?: Weekday[]
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
      fitnessLevel: form.fitnessLevel,
      sports: form.sports,
      trainingDays: form.trainingDays,
    }
    sessionStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(progress))
  } catch {
    // sessionStorage may be unavailable; silently ignore
  }
}

type FormData = {
  trainingGoal: UserProfile['trainingGoal']
  raceDate: string
  raceDescription: string
  // intervals.icu first (ai-trainer-ops#20): it is where this audience already
  // keeps its data, and it carries none of Strava's API-terms risk.
  assessmentMethod: 'intervals' | 'strava' | 'manual'
  // The three answers the planner needs most (ai-trainer-ops#43).
  sports: Sport[]
  trainingDays: Weekday[]
  currentFTP: string
  fitnessLevel: UserProfile['fitnessLevel']
  maxHeartRate: string
}

const SPORTS: { value: Sport; label: string }[] = [
  { value: 'cycling', label: 'Cycling' },
  { value: 'running', label: 'Running' },
  { value: 'strength', label: 'Strength' },
]

const WEEKDAYS: { value: Weekday; label: string }[] = [
  { value: 'monday', label: 'Mon' },
  { value: 'tuesday', label: 'Tue' },
  { value: 'wednesday', label: 'Wed' },
  { value: 'thursday', label: 'Thu' },
  { value: 'friday', label: 'Fri' },
  { value: 'saturday', label: 'Sat' },
  { value: 'sunday', label: 'Sun' },
]

/** Four days: a starting point to change with one tap, not a guess about the athlete.
 *
 *  No weekly hours (ai-trainer-ops#116). Most athletes do not know how much time
 *  they have, or misjudge it, and an untouched slider was saved as their word. The
 *  coach estimates the volume from what they actually train; a limit the athlete
 *  states in the chat still binds. */
const DEFAULT_TRAINING_DAYS: Weekday[] = ['tuesday', 'thursday', 'saturday', 'sunday']

function toggled<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
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
    setChatHistory,
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
      setChatHistory: s.setChatHistory,
    }))
  )
  const t = useT()

  const defaultAssessmentMethod = (): FormData['assessmentMethod'] => {
    const hasManualMetrics = Boolean(
      userProfile?.currentFTP || userProfile?.maxHeartRate
    )
    if (hasManualMetrics) return 'manual'
    if (intervalsConnection) return 'intervals'
    if (stravaConnection) return 'strava'
    // The recommended path, with its key form open, for a new account: a
    // tester who tapped "Weiter" on a preselected "Ich trage selbst ein" got a
    // plan built from nothing (ai-trainer-ops#213). One tap still picks manual.
    return 'intervals'
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
      sports: userProfile?.sports ?? ['cycling'],
      trainingDays: userProfile?.trainingDays ?? DEFAULT_TRAINING_DAYS,
      currentFTP: userProfile?.currentFTP ? String(userProfile.currentFTP) : '',
      fitnessLevel: userProfile?.fitnessLevel ?? 'intermediate',
      maxHeartRate: userProfile?.maxHeartRate ? String(userProfile.maxHeartRate) : '',
    }
  }

  // Restore progress saved before the Strava OAuth redirect (if any).
  const savedProgress = readOnboardingProgress()
  const [step, setStep] = useState(savedProgress?.step ?? 1)

  // Whether this athlete has to bring their own key, answered by the backend
  // rather than guessed from `hasGeminiKey` — the client cannot see whether
  // admin-key fallback is on or whether the owner set a global key
  // (ai-trainer-ops#41).
  //
  // `false` until the answer arrives, so a hosted athlete is never shown a step
  // that then vanishes. If the request fails it stays `false` and onboarding
  // behaves as it did before: the 402 from "Generate" is caught below and sends
  // the athlete to the key step anyway, so a failed probe costs a detour rather
  // than the dead end.
  const [keyRequired, setKeyRequired] = useState(false)
  // Whether the probe has answered yet. `keyRequired` starts false, which is
  // the right default to *render* but not something to make decisions on: the
  // difference between "no key is needed" and "we have not asked yet" matters
  // for a resumed session and for the generate shortcut below. Raised in review.
  const [keyStatusKnown, setKeyStatusKnown] = useState(false)
  // Whether the server has told us directly, rather than been asked in advance.
  //
  // Two things count: a 402 from `generateTrainingPlan`, which is proof a key
  // is needed, and the `keyRequired` that `PUT /users/me/ai-key` recomputes,
  // which is proof one is not. The on-mount probe is a forecast, and once we
  // hold either kind of proof a forecast still in flight must not overrule it.
  //
  // Both directions were raised in review. The 402 one reinstates the loop this
  // change removes: the probe resolves `false`, the redirect effect fires, and
  // the athlete is carried off the key step onto a summary about to 402 again.
  // The save one is milder — a stale `true` landing after a successful save
  // leaves the header reading "Step 6 of 6" behind a key step the athlete has
  // already finished — and the first version of this invited it, by *clearing*
  // the ref on save as though a save were less authoritative than a failure.
  //
  // A ref, not state: nothing renders from it, and it has to be readable by a
  // promise that resolves after the render it was created in.
  const keyStateIsAuthoritative = useRef(false)
  const [keyInput, setKeyInput] = useState('')
  const [keyTesting, setKeyTesting] = useState(false)
  const [keySaving, setKeySaving] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [keyTested, setKeyTested] = useState(false)
  // Why the athlete is suddenly on this step, when they got here by pressing
  // "Generate" rather than by walking the wizard. Without it the redirect below
  // swaps the summary — and the error that explained it — for a form asking
  // for a credential, with nothing connecting the two.
  const [keyPrompt, setKeyPrompt] = useState('')
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
      fitnessLevel: savedProgress.fitnessLevel,
      // Absent in progress saved before ai-trainer-ops#43.
      sports: savedProgress.sports ?? base.sports,
      trainingDays: savedProgress.trainingDays ?? base.trainingDays,
    }
  })

  // Back from the Strava OAuth redirect, the form already says 'strava'.
  const offerStrava = Boolean(stravaConnection) || form.assessmentMethod === 'strava'

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

  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    fetchAIKeyStatus(authToken)
      .then((status) => {
        if (cancelled) return
        // Never let a forecast overwrite what the server has already said.
        if (!keyStateIsAuthoritative.current) setKeyRequired(Boolean(status.keyRequired))
        setKeyStatusKnown(true)
      })
      .catch(() => {
        // Deliberately silent. Nothing the athlete can do about it, and the
        // generate path recovers on its own; a banner here would be noise on
        // the welcome screen of a flow that still works.
        //
        // Counted as "known" all the same, so a failed probe does not leave a
        // resumed session parked on the key step forever. The cost is a BYOK
        // athlete being sent to the summary and bounced back by the 402 — a
        // detour that heals itself, against a screen with no way off it.
        if (!cancelled) setKeyStatusKnown(true)
      })
    return () => {
      cancelled = true
    }
  }, [authToken])

  // Persist step and form to sessionStorage so the Strava OAuth redirect does not lose progress.
  useEffect(() => {
    saveOnboardingProgress(step, form)
  }, [step, form])

  const update = (key: keyof FormData, value: FormData[keyof FormData]) =>
    setForm((f) => ({ ...f, [key]: value }))

  // An analysis error belongs to the way it came from: after "I'll enter
  // sessions myself" the summary still said intervals.icu had refused the key
  // (ai-trainer-ops#212).
  const selectMethod = (method: FormData['assessmentMethod']) => {
    setError('')
    update('assessmentMethod', method)
  }

  const selectTrainingGoal = (trainingGoal: FormData['trainingGoal']) =>
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
      sports: form.sports,
      trainingDays: form.trainingDays,
      // No longer asked (ai-trainer-ops#43): it changed nothing the athlete could see.
      followsTrainingPlan: userProfile?.followsTrainingPlan ?? false,
      currentFTP: form.currentFTP ? Number(form.currentFTP) : undefined,
      fitnessLevel: form.fitnessLevel,
      maxHeartRate: resolvedMaxHR,
    }

    try {
      // An account onboarded already is not onboarding's to write: a failed
      // profile load used to land it here, and this saved defaults over its
      // race and generated a plan over its own (ai-trainer-ops#118). Asked at
      // click time; if the question fails, nothing is written either.
      if ((await fetchCurrentUser(authToken)).isOnboarded) {
        await useAppStore.getState().loadUserData()
        return
      }
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
      // That write is where the coach writes its first message
      // (ai-trainer-ops#153). The chat was loaded at sign-in, before it
      // existed, so read it again; without it the dashboard showed the
      // message only after a reload. Not worth failing onboarding over.
      try {
        setChatHistory(await fetchChatHistory(authToken))
      } catch (err) {
        console.warn('[Onboarding] Could not load the coach message', err)
      }

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
        keyStateIsAuthoritative.current = true
        setKeyRequired(true)
        // Reworded for where they are about to land. The backend's message says
        // "add your key in Settings → AI Provider", which is right when it is
        // read on the summary and wrong when it is read above the very form it
        // is asking for.
        setError('')
        setKeyPrompt(
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

  const connectedForAnalysis =
    (form.assessmentMethod === 'intervals' && Boolean(intervalsConnection)) ||
    (form.assessmentMethod === 'strava' && Boolean(stravaConnection))

  /** Check a key against the provider without storing it. */
  const handleTestKey = async () => {
    if (!authToken) return
    setKeyTesting(true)
    setKeyError('')
    setKeyTested(false)
    try {
      await testAIKey(authToken, 'gemini', keyInput.trim())
      setKeyTested(true)
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : 'Could not check the key.')
    } finally {
      setKeyTesting(false)
    }
  }

  /** Store the key, then move on — but only if the backend agrees it is enough.
   *
   * `PUT /users/me/ai-key` answers with a fresh `keyRequired`, so a key that was
   * accepted for a provider this deployment cannot reach does not advance the
   * flow. Trusting a 200 here would put the athlete back on "Generate" with the
   * same 402, which is the loop ai-trainer-ops#41 describes.
   */
  const handleSaveKey = async () => {
    if (!authToken) return
    setKeySaving(true)
    setKeyError('')
    try {
      const status = await saveAIKey(authToken, 'gemini', keyInput.trim())
      if (status.keyRequired) {
        setKeyError('Saved, but this deployment still cannot reach a model with it.')
        return
      }
      // Set, not cleared. The backend has just recomputed `keyRequired` for
      // this account and said no key is needed — which is the same kind of
      // answer as the 402, pointing the other way. Clearing it here let a probe
      // that was still in flight resolve with a stale `true` afterwards.
      keyStateIsAuthoritative.current = true
      setKeyRequired(false)
      setKeyInput('')
      setKeyTested(false)
      setKeyPrompt('')
      setStep(STEP_SUMMARY)
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : 'Could not save the key.')
    } finally {
      setKeySaving(false)
    }
  }

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
  // leading nowhere — Save was the only way out. Raised in review.
  useEffect(() => {
    if (keyStatusKnown && !keyRequired && step === STEP_AI_KEY) {
      setStep(STEP_SUMMARY)
    }
  }, [keyStatusKnown, keyRequired, step])

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

  const goals: {
    value: UserProfile['trainingGoal']
    label: string
    desc: string
    Icon: LucideIcon
  }[] = [
    { value: 'race', label: t('Race Prep'), desc: t('Be ready for a specific race'), Icon: Trophy },
    {
      value: 'general_fitness',
      label: t('General Fitness'),
      desc: t('Stay fit and healthy'),
      Icon: Dumbbell,
    },
  ]

  const levels: { value: UserProfile['fitnessLevel']; label: string; desc: string }[] = [
    { value: 'beginner', label: t('Beginner'), desc: t('Training < 1 year') },
    { value: 'intermediate', label: t('Intermediate'), desc: t('1-3 years experience') },
    { value: 'advanced', label: t('Advanced'), desc: t('3+ years, racing or structured training') },
  ]

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
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              {/* Position in the order, not the step's id: the key step is
                  numbered 6 and shown fifth, and on a hosted deployment it is
                  not shown at all. */}
              <span>
                {t('Step {position} of {total}', { position, total: steps.length })}
              </span>
              <span>{Math.round((position / steps.length) * 100)}%</span>
            </div>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all duration-300"
                style={{ width: `${(position / steps.length) * 100}%` }}
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
                {athleteName ? t('Welcome, {name}!', { name: athleteName }) : t('Welcome, athlete!')}
              </h2>
              {/* The owner's words (ai-trainer-ops#203), in place of the box
                  with three ticks that "looked like AI". What they promise
                  holds: every answer can be changed in Settings and in the
                  chat (#202). */}
              <p className="text-gray-500 mb-6 text-sm leading-relaxed">
                {t('We have 4 short questions to get you started. No worries if you are unsure, just talk to the trainer later to adapt or change it in your settings.')}
              </p>
              <p id="onboarding-sports-question" className="mb-3 text-sm font-semibold text-gray-800">
                {t('Which sports should your plan include?')}
              </p>
              <div className="flex flex-wrap justify-center gap-2" role="group" aria-labelledby="onboarding-sports-question">
                {SPORTS.map(({ value, label }) => {
                  const on = form.sports.includes(value)
                  return (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={on}
                      onClick={() => update('sports', toggled(form.sports, value))}
                      className={`rounded-full border-2 px-4 py-1.5 text-sm font-medium transition-all ${
                      on ? 'border-amber-500 bg-amber-50 text-amber-800' : 'border-gray-200 text-gray-600 hover:border-amber-300'
                    }`}
                    >
                      {t(label)}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* Step 2: Training goal */}
          {step === 2 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Are you preparing for a race?')}</h2>
              <p className="text-sm text-gray-500 mb-4">{t('Race prep adds a target date; otherwise your plan starts as general fitness.')}</p>
              <div className="space-y-2">
                {goals.map((g) => (
                  <button
                    key={g.value}
                    type="button"
                    // The tick beside the chosen option is the only thing that
                    // said which one it was, so the choice was invisible to a
                    // screen reader and unassertable in a test — the same
                    // colour-only problem #51.7 fixed on the leg-freshness
                    // indicator. The effort scale already does this
                    // (ai-trainer-ops#46).
                    aria-pressed={form.trainingGoal === g.value}
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
                    <label className="block text-sm font-medium text-gray-700 mb-1">{t('Race Date *')}</label>
                    <input
                      type="date"
                      value={form.raceDate}
                      onChange={(e) => update('raceDate', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">{t('Race Description')}</label>
                    <input
                      type="text"
                      value={form.raceDescription}
                      onChange={(e) => update('raceDescription', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder={t('e.g. 80km gran fondo with 2000m climbing')}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Step 3: Time (ai-trainer-ops#43). Level, FTP and max HR are optional
              and folded away: the coach estimates them from the first sessions. */}
          {step === 3 && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">{t('When can you train?')}</h2>
              <p className="text-sm text-gray-500 mb-4">{t('A starting point, not a fixed rule: the coach plans on these days and learns from your training how much time you have. Tell the coach if a day generally works better for you than another, for example Monday rather than Tuesday, or if a week looks different.')}</p>
              <div className="space-y-5">
                <div>
                  <p className="block text-sm font-medium text-gray-700 mb-2">{t('Days you can train')}</p>
                  <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('Days you can train')}>
                    {WEEKDAYS.map(({ value, label }) => {
                      const on = form.trainingDays.includes(value)
                      return (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={on}
                          onClick={() => update('trainingDays', toggled(form.trainingDays, value))}
                          className={`w-11 rounded-lg border-2 py-1.5 text-sm font-medium transition-all ${
                            on ? 'border-amber-500 bg-amber-50 text-amber-800' : 'border-gray-200 text-gray-500 hover:border-amber-300'
                          }`}
                        >
                          {t(label)}
                        </button>
                      )
                    })}
                  </div>
                  {form.trainingDays.length === 0 && (
                    <p className="mt-1 text-xs text-red-600">{t('Pick at least one day.')}</p>
                  )}
                </div>
                <details className="rounded-lg border border-gray-200 px-4 py-3">
                  <summary className="cursor-pointer text-sm font-medium text-gray-700">
                    {t('Optional: level, FTP, max heart rate')}
                  </summary>
                  <p className="mt-2 mb-4 text-xs text-gray-500">
                    {t('Skip these if you are not sure. The coach estimates them from your first sessions.')}
                  </p>
                  <div className="space-y-5">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">{t('Fitness Level')}</label>
                  <div className="space-y-2">
                    {levels.map((l) => (
                      <button
                        key={l.value}
                        type="button"
                        aria-pressed={form.fitnessLevel === l.value}
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
                      {t('Current FTP (watts)')} <span className="text-gray-400 font-normal">optional</span>
                    </label>
                    <input
                      type="number"
                      value={form.currentFTP}
                      onChange={(e) => update('currentFTP', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder={t('e.g. {example}', { example: 250 })}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {t('Max Heart Rate (bpm)')} <span className="text-gray-400 font-normal">optional</span>
                    </label>
                    <input
                      type="number"
                      value={form.maxHeartRate}
                      onChange={(e) => update('maxHeartRate', e.target.value)}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                      placeholder={t('e.g. {example}', { example: 185 })}
                    />

                  </div>
                </div>
                  </div>
                </details>
              </div>
            </div>
          )}

          {/* Step 4: Fitness assessment method */}
          {step === 4 && (
            <div>
              {loading ? (
                <div className="text-center py-10">
                  <Loader2 size={32} className="animate-spin text-amber-500 mx-auto mb-3" />
                  <p className="font-semibold text-gray-900">{t('Analysing rides and generating your plan…')}</p>
                  <p className="text-sm text-gray-500 mt-1">{t('This takes about 30 seconds.')}</p>
                </div>
              ) : (
                <>
                  <h2 className="text-xl font-bold text-gray-900 mb-1">{t('How should we assess your fitness?')}</h2>
                  <p className="text-sm text-gray-500 mb-4">
                    {t('Connect intervals.icu for automatic analysis of your last 7 rides, or log your sessions yourself.')}
                  </p>
                  <div className="space-y-3">
                    <button
                      type="button"
                      aria-pressed={form.assessmentMethod === 'intervals'}
                      onClick={() => selectMethod('intervals')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'intervals'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">{t('Connect intervals.icu')}</p>
                          <p className="text-xs text-gray-500 mt-1">
                            {t('Recommended. Analyses your last 7 rides with your intervals.icu API key.')}
                          </p>
                          <p className="text-xs text-gray-500 mt-1">
                            {t('On Strava? Connect your Garmin, Wahoo or other device to intervals.icu (free) as well: rides that come only through Strava do not reach us.')}
                          </p>
                        </div>
                        {form.assessmentMethod === 'intervals' && (
                          <CheckCircle size={16} className="text-amber-500 shrink-0" />
                        )}
                      </div>
                    </button>
                    {/* Strava is offered only to an athlete already connected (ai-trainer-ops#39 Q6):
                        new athletes come through intervals.icu, existing connections keep working. */}
                    {offerStrava && (
                    <button
                      type="button"
                      aria-pressed={form.assessmentMethod === 'strava'}
                      onClick={() => selectMethod('strava')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'strava'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">{t('Connect with Strava')}</p>
                          <p className="text-xs text-gray-500 mt-1">
                            {t('Analyses your last 7 rides to estimate FTP and training zones.')}
                          </p>
                        </div>
                        {form.assessmentMethod === 'strava' && (
                          <CheckCircle size={16} className="text-amber-500 shrink-0" />
                        )}
                      </div>
                    </button>
                    )}
                    <button
                      type="button"
                      aria-pressed={form.assessmentMethod === 'manual'}
                      onClick={() => selectMethod('manual')}
                      className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                        form.assessmentMethod === 'manual'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-gray-200 hover:border-amber-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-semibold text-sm text-gray-900">{t("I'll enter sessions myself")}</p>
                          <p className="text-xs text-gray-500 mt-1">
                            {t('The coach starts from your answers and learns from the sessions you log.')}
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
                      <IntervalsConnect onChange={() => setError('')} />
                      {!intervalsConnection && (
                        <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                          <Link size={14} className="shrink-0" />
                          {t('Connect intervals.icu to continue with automatic activity analysis.')}
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
                          {t('Connect Strava to continue with automatic activity analysis.')}
                        </div>
                      )}
                    </div>
                  )}
                  {error && (
                    <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mt-4">
                      {t(error)}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Step 5: Summary */}
          {/* The key step (ai-trainer-ops#41). Only reachable when the backend
              says this athlete must supply a key, so the copy can state that
              plainly instead of hedging. */}
          {step === STEP_AI_KEY && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Add your Gemini key')}</h2>
              {keyPrompt && (
                <p className="mb-3 flex items-start gap-1.5 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                  {t(keyPrompt)}
                </p>
              )}
              <p className="text-sm text-gray-500 mb-4">
                {t('This deployment does not provide a model, so your coach runs on your own Google Gemini key. It stays on this server, encrypted at rest, and is used only for your own plans and chats.')}
              </p>

              <a
                href="https://aistudio.google.com/apikey"
                target="_blank"
                rel="noreferrer"
                className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-amber-600 hover:text-amber-700"
              >
                <Link size={14} aria-hidden="true" />
                {t('Get a key — it takes about two minutes')}
              </a>

              <label
                htmlFor="onboarding-ai-key"
                className="mb-1 block text-sm font-medium text-gray-700"
              >
                {t('Gemini API key')}
              </label>
              <input
                id="onboarding-ai-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={keyInput}
                onChange={(e) => {
                  setKeyInput(e.target.value)
                  // A key that was tested and then edited has not been tested.
                  setKeyTested(false)
                  setKeyError('')
                }}
                placeholder="AIza…"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-amber-500 focus:ring-amber-500"
              />

              {keyError && (
                <p className="mt-2 flex items-start gap-1.5 text-sm text-red-600">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                  {t(keyError)}
                </p>
              )}
              {keyTested && !keyError && (
                <p className="mt-2 flex items-center gap-1.5 text-sm text-emerald-700">
                  <CheckCircle size={14} className="shrink-0" aria-hidden="true" />
                  {t('The key works.')}
                </p>
              )}

              <div className="mt-4 flex gap-3">
                <button
                  type="button"
                  onClick={() => void handleTestKey()}
                  disabled={!keyInput.trim() || keyTesting || keySaving}
                  className="flex-1 rounded-xl border border-gray-300 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
                >
                  {keyTesting ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                      {t('Testing…')}
                    </span>
                  ) : (
                    t('Test key')
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSaveKey()}
                  disabled={!keyInput.trim() || keyTesting || keySaving}
                  className="flex-1 rounded-xl bg-amber-500 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-amber-600 disabled:opacity-50"
                >
                  {keySaving ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                      {t('Saving…')}
                    </span>
                  ) : (
                    t('Save and continue')
                  )}
                </button>
              </div>

              <p className="mt-3 text-xs text-gray-400">
                {t('You can change or remove it later under Settings → AI Provider.')}
              </p>
            </div>
          )}

          {step === STEP_SUMMARY && (
            <div>
              <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Ready to Go!')}</h2>
              <p className="text-sm text-gray-500 mb-4">
                {t('Review your inputs and generate your first 14-day plan.')}
              </p>

              <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm mb-4">
                {(() => {
                  const displayMaxHR = form.maxHeartRate ? t('{n} bpm', { n: form.maxHeartRate }) : null
                  return (
                    [
                      // The stored values, translated as they are: 'cycling', 'general fitness'.
                      [t('Sports'), form.sports.map((sport) => t(sport)).join(', ')],
                      [t('Goal'), t(form.trainingGoal.replace('_', ' '))],
                      [t('Training days'), WEEKDAYS.filter((d) => form.trainingDays.includes(d.value)).map((d) => t(d.label)).join(' ')],
                      [t('Assessment'), t(form.assessmentMethod === 'intervals' ? 'intervals.icu (last 7 rides)' : form.assessmentMethod === 'strava' ? 'Strava (last 7 rides)' : 'Manual')],
                      ...(form.raceDate ? [[t('Race Date'), form.raceDate]] : []),
                      [t('Fitness Level'), t(form.fitnessLevel)],
                      ...(form.currentFTP ? [['FTP', `${form.currentFTP}W`]] : []),
                      ...(displayMaxHR ? [[t('Max HR'), displayMaxHR]] : []),
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
                  {t(error)}
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
                    {t('Generating your plan...')}
                  </>
                ) : (
                  <>
                    <Target size={18} />
                    {t('Generate My 14-Day Training Plan')}
                  </>
                )}
              </button>
            </div>
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
