import type { IntervalsConnection, Sport, StravaConnection, UserProfile, Weekday } from '../store/useAppStore'
import { ONBOARDING_STORAGE_KEY } from '../lib/onboardingStorage'

// What onboarding asks and keeps (ai-trainer-ops#274: out of `OnboardingPage`).

/** The assessment step, from which a connected athlete generates directly. */
export const STEP_ASSESSMENT = 4
/** "Ready to Go!" — the summary, and the only step that generates a plan. */
export const STEP_SUMMARY = 5
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
export const STEP_AI_KEY = 6

/** The four questions the welcome text promises. The key step and the summary
 *  are no question, and the header gives them no number (ai-trainer-ops#223). */
export const QUESTION_STEPS = [1, 2, 3, STEP_ASSESSMENT]

/** The steps in the order they are shown, which depends on the deployment.
 *
 * In BYOK-only mode the key step sits between the assessment and the summary:
 * after the questions, so nothing is asked for before the athlete knows what
 * they are getting, and before the summary, so "Generate" is never a button
 * that cannot work. On a hosted deployment it is not in the list at all.
 */
export function stepsFor(keyRequired: boolean): number[] {
  return keyRequired
    ? [1, 2, 3, STEP_ASSESSMENT, STEP_AI_KEY, STEP_SUMMARY]
    : [1, 2, 3, STEP_ASSESSMENT, STEP_SUMMARY]
}
// Only non-sensitive fields are persisted across the OAuth redirect.
// Health metrics (HR, FTP) are intentionally excluded and re-populated
// from the user profile on restore to avoid clear-text health data in storage.
export type PersistedProgress = {
  step: number
  trainingGoal: OnboardingForm['trainingGoal']
  raceDate: string
  raceDescription: string
  assessmentMethod: OnboardingForm['assessmentMethod']
  fitnessLevel: OnboardingForm['fitnessLevel']
  sports?: Sport[]
  trainingDays?: Weekday[]
}

export function readOnboardingProgress(): PersistedProgress | null {
  try {
    const raw = sessionStorage.getItem(ONBOARDING_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedProgress) : null
  } catch {
    return null
  }
}

export function saveOnboardingProgress(step: number, form: OnboardingForm): void {
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

export type OnboardingForm = {
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

export const SPORTS: { value: Sport; label: string }[] = [
  { value: 'cycling', label: 'Cycling' },
  { value: 'running', label: 'Running' },
  { value: 'strength', label: 'Strength' },
]

export const WEEKDAYS: { value: Weekday; label: string }[] = [
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
export const DEFAULT_TRAINING_DAYS: Weekday[] = ['tuesday', 'thursday', 'saturday', 'sunday']

export function toggled<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

/** Days in week order, not tap order: the first dashboard read "Di, Do, Sa,
 *  So, Mi" until the next profile load sorted it (ai-trainer-ops#233). */
export const inWeekOrder = (days: Weekday[]) => WEEKDAYS.map((d) => d.value).filter((d) => days.includes(d))

function normalizeTrainingGoal(goal: unknown): OnboardingForm['trainingGoal'] {
  return goal === 'race' ? 'race' : 'general_fitness'
}

type Connections = { strava: StravaConnection | null; intervals: IntervalsConnection | null }

function defaultAssessmentMethod(userProfile: UserProfile | null, connections: Connections): OnboardingForm['assessmentMethod'] {
  const hasManualMetrics = Boolean(userProfile?.currentFTP || userProfile?.maxHeartRate)
  if (hasManualMetrics) return 'manual'
  if (connections.intervals) return 'intervals'
  if (connections.strava) return 'strava'
  // The recommended path, with its key form open, for a new account: a
  // tester who tapped "Weiter" on a preselected "Ich trage selbst ein" got a
  // plan built from nothing (ai-trainer-ops#213). One tap still picks manual.
  return 'intervals'
}

function defaultForm(userProfile: UserProfile | null, connections: Connections): OnboardingForm {
  const trainingGoal: OnboardingForm['trainingGoal'] =
    userProfile?.trainingGoal === 'race' || userProfile?.raceDate ? 'race' : 'general_fitness'
  return {
    trainingGoal,
    raceDate: trainingGoal === 'race' ? userProfile?.raceDate ?? '' : '',
    raceDescription: trainingGoal === 'race' ? userProfile?.raceDescription ?? '' : '',
    assessmentMethod: defaultAssessmentMethod(userProfile, connections),
    sports: userProfile?.sports ?? ['cycling'],
    trainingDays: userProfile?.trainingDays ?? DEFAULT_TRAINING_DAYS,
    currentFTP: userProfile?.currentFTP ? String(userProfile.currentFTP) : '',
    // None until the athlete picks one: the question is folded away, and an
    // unasked "intermediate" was told to the coach as theirs (ai-trainer-ops#214).
    fitnessLevel: userProfile?.fitnessLevel ?? null,
    maxHeartRate: userProfile?.maxHeartRate ? String(userProfile.maxHeartRate) : '',
  }
}

/** The form to start from: the profile's answers, with the progress saved
 *  before a Strava redirect on top. */
export function initialForm(
  userProfile: UserProfile | null,
  connections: Connections,
  saved: PersistedProgress | null,
): OnboardingForm {
  const base = defaultForm(userProfile, connections)
  if (!saved) return base
  const trainingGoal = normalizeTrainingGoal(saved.trainingGoal)
  // Merge persisted non-sensitive fields with health metrics from the user profile.
  return {
    ...base,
    trainingGoal,
    raceDate: trainingGoal === 'race' ? saved.raceDate : '',
    raceDescription: trainingGoal === 'race' ? saved.raceDescription : '',
    assessmentMethod: saved.assessmentMethod,
    fitnessLevel: saved.fitnessLevel,
    // Absent in progress saved before ai-trainer-ops#43.
    sports: saved.sports ?? base.sports,
    trainingDays: saved.trainingDays ?? base.trainingDays,
  }
}
