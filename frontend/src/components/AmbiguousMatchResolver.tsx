import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { setPlan, usePlan } from '../queries/plan'
import { chip, note } from './ui'
import { resolveRideMatch } from '../services/ai'
import { fetchRideMetricsHistory, fetchTrainingPlan } from '../services/user'
import { sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { useT } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'

/**
 * Lets the athlete say which planned session an ambiguous ride actually was (#574).
 *
 * `apply_ride_plan_matches` produces `ambiguous` on purpose — a structured hard day
 * with several rides, or a day mixing sports — rather than guessing. That refusal is
 * the right call, but until this component existed it was a dead end: the calendar
 * showed an amber dot, `POST /ai/resolve-ride-match` sat there unused, and the ride
 * never became attributable, never got a coach review, and never marked its session
 * completed.
 *
 * Renders nothing unless *this* ride is ambiguous and the day has a planned session,
 * so it costs a normal activity row nothing.
 */
export default function AmbiguousMatchResolver({ ride }: { ride: RideMetricPoint }) {
  const trainingPlan = usePlan()
  const { authToken, updateRideMetric, setRideMetricsHistory } =
    useAppStore(
      useShallow((s) => ({
        authToken: s.authToken,
        updateRideMetric: s.updateRideMetric,
        setRideMetricsHistory: s.setRideMetricsHistory,
      }))
    )
  const [coachNote, setCoachNote] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)
  const t = useT()
  const planText = usePlanText()

  const sessions = sessionsForDate(trainingPlan, ride.activityDate)

  // Deliberately plain state rather than react-query: this is a one-shot action, and
  // `useMutation` would force every screen that embeds an activity row to sit under a
  // QueryClientProvider it does not otherwise need.
  const resolve = async (session: TrainingDay) => {
    if (!authToken || pending) return
    setPending(true)
    setFailed(false)
    try {
      const result = await resolveRideMatch(
        authToken,
        ride.activityDate,
        ride.stravaActivityId,
        sessionSlot(session),
        ride.externalActivityId
      )
      updateRideMetric(result.ride)
      setCoachNote(result.coachNote ?? null)
      // Resolving also unmatches whatever else was claiming that session, and marks
      // the session completed — neither of which is in this response. Re-read both
      // so the amber dot on the *other* ride and the calendar tick are not stale.
      const [rides, plan] = await Promise.all([
        fetchRideMetricsHistory(authToken),
        fetchTrainingPlan(authToken),
      ])
      setRideMetricsHistory(rides)
      setPlan(plan)
    } catch {
      setFailed(true)
    } finally {
      setPending(false)
    }
  }

  if (ride.planMatchStatus !== 'ambiguous' || sessions.length === 0 || !authToken) {
    return null
  }

  const rideName = ride.activityName ?? t('this activity')
  const multiple = sessions.length > 1

  // Part of the ride's row, in its text: no yellow box, no icons (owner's
  // review, 2026-10-24).
  return (
    <div className="mt-2 text-sm">
      {coachNote ? (
        <p className="text-gray-700">{coachNote}</p>
      ) : (
        <>
          <p className="font-medium text-gray-900">
            {multiple ? t('Which session was this?') : t('Was this your planned session?')}
          </p>
          <p className="text-gray-500">
            {multiple
              ? t('More than one ride could be these sessions, so this was left for you rather than guessed.')
              : t('More than one ride could be this session, so this was left for you rather than guessed.')}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {sessions.map((session) => {
              const label = sessionLabel(session, sessions.length)
              const title = planText(session.title) || t(session.workoutType)
              return (
                <button
                  key={`${session.date}#${sessionSlot(session)}`}
                  type="button"
                  disabled={pending}
                  // Two ambiguous rides on one day are the normal case here, so the
                  // ride has to be in the label — otherwise a screen reader offers
                  // the same choice twice with no way to tell them apart.
                  aria-label={t('Match “{ride}” to {session}', { ride: rideName, session: `${label ? `${label} ` : ''}${title}` })}
                  onClick={() => void resolve(session)}
                  className={`${chip(false)} inline-flex items-center gap-1.5 disabled:opacity-50`}
                >
                  {label && <span className="text-gray-500">{label}</span>}
                  <span className="truncate max-w-[10rem]">{multiple ? title : t('Yes — {title}', { title })}</span>
                </button>
              )
            })}
          </div>
        </>
      )}

      {pending && <p className={`mt-2 ${note(true)}`}>{t('Matching it up and asking the coach to look…')}</p>}
      {failed && <p className={`mt-2 ${note(false)}`}>{t('Could not save that. Try again in a moment.')}</p>}
    </div>
  )
}
