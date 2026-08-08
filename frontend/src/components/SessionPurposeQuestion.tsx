import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { Check, HelpCircle, Loader2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { answerRidePurpose, type RidePurposeAnswer } from '../services/user'
import type { RideMetricPoint } from '../store/useAppStore'

/**
 * Lets the athlete say what a session the classifier could not read actually was (#580).
 *
 * The coach already asked this — as a sentence inside the login summary, rendered
 * as body text in a card. There was no answer field, no state and no consequence:
 * the athlete could not reply anywhere, and the coach never learned the answer. A
 * question the coach says it needs answered in order to work should be an object
 * with a lifecycle, not prose in a paragraph.
 *
 * The answer belongs on the *session*, not on the athlete: "Thursday was 4×8 at
 * threshold" is a fact about one activity, so it writes back to the ride's own
 * classification rather than through the memory-fact channel an `AthleteInquiry`
 * answer takes.
 *
 * Renders nothing unless this ride's question is open, so a normal activity row
 * costs nothing. `purposeQuestionOpen` is decided on the backend — the rule for
 * what counts as unresolved lives with the prompts that depend on it.
 */

/**
 * Mirrors `ATHLETE_PURPOSE_CHOICES` in `services/ride_purpose_question.py`; the
 * backend schema rejects anything else. Ordered easy → hard so the list reads as
 * a scale rather than as an arbitrary menu.
 */
const PURPOSE_OPTIONS: ReadonlyArray<{ value: RidePurposeAnswer; label: string }> = [
  { value: 'recovery', label: 'Recovery' },
  { value: 'endurance', label: 'Endurance' },
  { value: 'tempo', label: 'Tempo' },
  { value: 'interval_sweetspot', label: 'Sweet-spot intervals' },
  { value: 'interval_threshold', label: 'Threshold intervals' },
  { value: 'interval_vo2max', label: 'VO2max intervals' },
  { value: 'interval_sprints', label: 'Sprints' },
  { value: 'mixed', label: 'Mixed intervals' },
]

export default function SessionPurposeQuestion({ ride }: { ride: RideMetricPoint }) {
  const { authToken, updateRideMetric } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      updateRideMetric: s.updateRideMetric,
    }))
  )
  const [pending, setPending] = useState<RidePurposeAnswer | 'skip' | null>(null)
  const [answered, setAnswered] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  // Deliberately plain state rather than react-query, for the same reason
  // AmbiguousMatchResolver is: this is a one-shot action, and `useMutation` would
  // force every screen that embeds an activity row under a QueryClientProvider it
  // does not otherwise need.
  const send = async (purpose: RidePurposeAnswer | null) => {
    if (!authToken || pending) return
    setPending(purpose ?? 'skip')
    setFailed(false)
    try {
      const result = await answerRidePurpose(
        authToken,
        ride.stravaActivityId,
        purpose,
        ride.externalActivityId
      )
      if (result.ride) updateRideMetric(result.ride)
      if (purpose) {
        setAnswered(PURPOSE_OPTIONS.find((o) => o.value === purpose)?.label ?? purpose)
      }
    } catch {
      setFailed(true)
    } finally {
      setPending(null)
    }
  }

  if (!ride.purposeQuestionOpen || !authToken) return null

  // The store update above closes the question, so this only shows on the render
  // between answering and the row re-reading — long enough to confirm the tap
  // landed, which a card that simply vanishes never does.
  if (answered) {
    return (
      <div
        data-testid="session-purpose-answered"
        className="mt-1.5 flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800"
      >
        <Check size={13} className="flex-shrink-0" />
        Noted — {answered}. Your coach will use that.
      </div>
    )
  }

  const rideName = ride.activityName ?? 'this activity'

  return (
    <div
      data-testid="session-purpose-question"
      className="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2"
    >
      <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-800">
        <HelpCircle size={13} className="flex-shrink-0" />
        What was this session?
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-amber-700">
        There was not enough data to work it out, so your coach is asking rather than guessing.
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {PURPOSE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={pending !== null}
            // Several unclassified activities on one screen is the normal case
            // here, so the ride has to be in the label — otherwise a screen
            // reader offers the same eight choices repeatedly with no way to
            // tell which session they belong to.
            aria-label={`“${rideName}” was ${option.label}`}
            onClick={() => void send(option.value)}
            className="inline-flex items-center rounded-lg border border-amber-300 bg-white px-2 py-1 text-xs font-semibold text-amber-800 transition-colors hover:bg-amber-100 disabled:opacity-50"
          >
            {option.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        disabled={pending !== null}
        aria-label={`Skip the question about “${rideName}”`}
        onClick={() => void send(null)}
        className="mt-1.5 rounded-lg px-1 py-0.5 text-[11px] font-medium text-amber-700 transition-colors hover:text-amber-900 disabled:opacity-50"
      >
        I'd rather not say
      </button>

      {pending !== null && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-amber-700">
          <Loader2 size={12} className="animate-spin" />
          Saving…
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
