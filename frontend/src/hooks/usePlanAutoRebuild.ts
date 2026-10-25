import { useEffect, useRef } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { setPlan } from '../queries/plan'
import type { RaceEvent, UserProfile } from '../store/useAppStore'
import { fetchPlanInputsChanged, rebuildPlanIfChanged, refreshLoginSummary } from '../services/ai'
import { useT } from '../i18n'

/** How long after the last change the rebuild is asked for: several changes in
 *  a row (three chips in Settings) give one rebuild, not three. */
export const REBUILD_SETTLE_MS = 2500

/** The reasons the athlete made by hand (``plan_inputs.ATHLETE_REASONS``). */
const ATHLETE_REASONS = new Set(['training_days', 'sports', 'level', 'ftp', 'race', 'hours'])

/** What the plan is built from, as far as the athlete can change it here. */
function inputsSignature(profile: UserProfile | null, raceEvents: RaceEvent[]): string {
  if (!profile) return ''
  return JSON.stringify([
    [...(profile.trainingDays ?? [])].sort(),
    [...(profile.sports ?? [])].sort(),
    profile.fitnessLevel,
    profile.currentFTP ?? null,
    profile.weeklyHours ?? null,
    profile.trainingGoal,
    profile.raceDate ?? null,
    raceEvents.map((e) => [e.date, e.distanceKm, e.elevationM]).sort(),
  ])
}

/**
 * The plan follows a change the athlete made, by itself (ai-trainer-ops#206).
 *
 * The owner: the rebuild card "sollte anders passieren. der coach sollte das
 * einfach selbst machen". When the athlete saves a setting, accepts a chat
 * offer or adds a race, this asks the server to rebuild; the server rebuilds
 * only for their own changes, within the day's cap. New rides and answered
 * questions wait for the nightly run. Mounted once, in the signed-in layout, so
 * a change in Settings rebuilds without a visit to the dashboard.
 */
export function usePlanAutoRebuild() {
  const t = useT()
  const { authToken, profile, raceEvents, isOnboarded, setPlanRebuild, showWarning, setRiderAssessment } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      profile: s.userProfile,
      raceEvents: s.raceEvents,
      isOnboarded: s.isOnboarded,
      setPlanRebuild: s.setPlanRebuild,
      showWarning: s.showWarning,
      setRiderAssessment: s.setRiderAssessment,
    }))
  )
  const signature = inputsSignature(profile, raceEvents)
  const seen = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inFlight = useRef(false)
  // A change saved while a rebuild is on its way gets one more ask afterwards.
  const again = useRef(false)

  useEffect(() => {
    if (!authToken || !isOnboarded || !signature) return
    const first = seen.current === null
    if (seen.current === signature) return
    seen.current = signature

    const rebuild = async () => {
      if (inFlight.current) {
        again.current = true
        return
      }
      inFlight.current = true
      try {
        // Asked first, which costs nothing: a profile or a race list that only
        // finished loading is no change, and the rebuild itself spends from
        // the app's AI allowance (ai-trainer-ops#183).
        const changed = await fetchPlanInputsChanged(authToken)
        if (!changed.reasons.some((reason) => ATHLETE_REASONS.has(reason))) return
        const result = await rebuildPlanIfChanged(authToken)
        if (result.rebuilt && result.plan) {
          setPlan(result.plan)
          setPlanRebuild({ at: new Date().toISOString(), reasons: result.reasons })
          // The server drops the training summary with the old plan, and the card
          // kept describing the old week until the next load (ai-trainer-ops#273).
          // The same as the chat does after its plan changes; there is no
          // summary before the first assessment.
          if (useAppStore.getState().riderAssessment) {
            refreshLoginSummary(authToken)
              .then((loginSummary) => {
                const current = useAppStore.getState().riderAssessment
                if (loginSummary && current) setRiderAssessment({ ...current, loginSummary })
              })
              .catch(() => {
                // The old summary stays until the next dashboard load regenerates it.
              })
          }
        }
      } catch {
        // The nightly run rebuilds what is still waiting; the athlete hears so
        // rather than nothing (ai-trainer-ops#272).
        showWarning(t('Your plan could not be rebuilt after your change. It is rebuilt tonight.'))
      } finally {
        inFlight.current = false
        if (again.current) {
          again.current = false
          void rebuild()
        }
      }
    }

    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void rebuild(), first ? 0 : REBUILD_SETTLE_MS)
  }, [authToken, isOnboarded, signature, setPlanRebuild, setRiderAssessment])

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    []
  )
}
