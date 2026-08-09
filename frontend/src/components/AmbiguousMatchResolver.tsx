import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { Bot, HelpCircle, Loader2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { resolveRideMatch } from '../services/ai'
import { fetchRideMetricsHistory, fetchTrainingPlan } from '../services/user'
import { sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'

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
  const { authToken, trainingPlan, updateRideMetric, setTrainingPlan, setRideMetricsHistory } =
    useAppStore(
      useShallow((s) => ({
        authToken: s.authToken,
        trainingPlan: s.trainingPlan,
        updateRideMetric: s.updateRideMetric,
        setTrainingPlan: s.setTrainingPlan,
        setRideMetricsHistory: s.setRideMetricsHistory,
      }))
    )
  const [coachNote, setCoachNote] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

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
      setTrainingPlan(plan)
    } catch {
      setFailed(true)
    } finally {
      setPending(false)
    }
  }

  if (ride.planMatchStatus !== 'ambiguous' || sessions.length === 0 || !authToken) {
    return null
  }

  const rideName = ride.activityName ?? 'this activity'
  const multiple = sessions.length > 1

  return (
    <div className="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2">
      {coachNote ? (
        <p className="flex items-start gap-1.5 text-xs text-amber-900 leading-relaxed">
          <Bot size={13} className="mt-0.5 flex-shrink-0 text-amber-600" />
          {coachNote}
        </p>
      ) : (
        <>
          <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-800">
            <HelpCircle size={13} className="flex-shrink-0" />
            {multiple ? 'Which session was this?' : 'Was this your planned session?'}
          </p>
          <p className="mt-0.5 text-[11px] text-amber-700 leading-snug">
            {multiple
              ? 'More than one ride could be these sessions, so this was left for you rather than guessed.'
              : 'More than one ride could be this session, so this was left for you rather than guessed.'}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {sessions.map((session) => {
              const label = sessionLabel(session, sessions.length)
              const title = session.title || session.workoutType
              return (
                <button
                  key={`${session.date}#${sessionSlot(session)}`}
                  type="button"
                  disabled={pending}
                  // Two ambiguous rides on one day are the normal case here, so the
                  // ride has to be in the label — otherwise a screen reader offers
                  // the same choice twice with no way to tell them apart.
                  aria-label={`Match “${rideName}” to ${label ? `${label} ` : ''}${title}`}
                  onClick={() => void resolve(session)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-2 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-50 transition-colors"
                >
                  {label && <span className="text-[10px] font-bold uppercase opacity-70">{label}</span>}
                  <span className="truncate max-w-[10rem]">{multiple ? title : `Yes — ${title}`}</span>
                </button>
              )
            })}
          </div>
        </>
      )}

      {pending && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-amber-700">
          <Loader2 size={12} className="animate-spin" />
          Matching it up and asking the coach to look…
        </p>
      )}
      {failed && (
        <p className="mt-1.5 text-[11px] font-medium text-red-600">
          Could not save that. Try again in a moment.
        </p>
      )}
    </div>
  )
}
