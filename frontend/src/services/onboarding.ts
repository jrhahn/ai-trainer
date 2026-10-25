import type { ChatMessage, RiderAssessment, TrainingDay, UserProfile } from '../types/domain'
import { analyseStravaActivities, generateTrainingPlan } from './ai'
import { getIntervalsActivities } from './intervals'
import { getStravaActivities } from './strava'
import { fetchChatHistory, fetchCurrentUser, saveTrainingPlan, updateCurrentUser } from './user'

/** What onboarding's "Generate" wrote, for the store (ai-trainer-ops#274: out of `OnboardingPage`). */
export interface FirstPlan {
  plan: TrainingDay[]
  riderAssessment?: RiderAssessment
  stravaAnalysisComplete: boolean
  intervalsAnalysisComplete: boolean
  /** Null when it could not be loaded: not worth failing onboarding over. */
  chatHistory: ChatMessage[] | null
}

/**
 * Analyse the last seven rides from `source`, if any, then store the profile and
 * the first plan, and mark the account onboarded only once the plan exists.
 *
 * Null when the account was onboarded already, and then nothing is written.
 */
export async function generateFirstPlan(
  token: string,
  profile: UserProfile,
  source: 'intervals' | 'strava' | null,
): Promise<FirstPlan | null> {
  // An account onboarded already is not onboarding's to write: a failed
  // profile load used to land it here, and this saved defaults over its
  // race and generated a plan over its own (ai-trainer-ops#118). Asked at
  // click time; if the question fails, nothing is written either.
  if ((await fetchCurrentUser(token)).isOnboarded) return null

  let riderAssessment: RiderAssessment | undefined
  let stravaAnalysisComplete = false
  let intervalsAnalysisComplete = false
  // The same seven-ride analysis whichever service the rides come from; the
  // backend reads `source` to know which activity identity it is looking at.
  if (source) {
    const activities = source === 'intervals' ? await getIntervalsActivities(token) : await getStravaActivities(token)
    const recentActivities = activities.slice(0, 7)
    if (recentActivities.length > 0) {
      const analyseResult = await analyseStravaActivities(
        recentActivities,
        token,
        profile.maxHeartRate,
        profile.currentFTP,
        source,
      )
      riderAssessment = analyseResult.assessment
      if (source === 'intervals') {
        intervalsAnalysisComplete = true
      } else {
        stravaAnalysisComplete = true
      }
    }
  }

  // The profile first, without `isOnboarded` — name and email are not
  // onboarding's to write (ai-trainer-ops#40).
  await updateCurrentUser(token, {
    ...profile,
    name: undefined,
    email: undefined,
    stravaAnalysisComplete,
    intervalsAnalysisComplete,
  })
  const plan = await generateTrainingPlan(token)
  await saveTrainingPlan(token, plan)

  // `isOnboarded` last, and only once a plan exists on the server
  // (ai-trainer-ops#41). It used to go out with the profile above, so a
  // failing plan generation — a 402 for a missing AI key is the ordinary
  // case in BYOK-only mode — left the account marked onboarded with no
  // plan. The error was shown here and the local flag stayed false, so it
  // looked survivable; a reload then read `isOnboarded` from the server and
  // landed on the dashboard saying "No session planned for today", with
  // nothing anywhere naming the cause. Failing before this line instead
  // keeps the athlete in onboarding, where the message is.
  await updateCurrentUser(token, { isOnboarded: true })
  // That write is where the coach writes its first message
  // (ai-trainer-ops#153). The chat was loaded at sign-in, before it
  // existed, so read it again; without it the dashboard showed the
  // message only after a reload. Not worth failing onboarding over.
  let chatHistory: ChatMessage[] | null = null
  try {
    chatHistory = await fetchChatHistory(token)
  } catch (err) {
    console.warn('[Onboarding] Could not load the coach message', err)
  }
  return { plan, riderAssessment, stravaAnalysisComplete, intervalsAnalysisComplete, chatHistory }
}
