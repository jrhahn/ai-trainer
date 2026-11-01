import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'
import { useLanguage, useT, type Translate } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { dotted } from './ui'

// The planning priors a day can still break once the coach was asked to fix it
// (ai-trainer-ops#87), and the athlete's states (ai-trainer-ops#112). The
// owner's choice was a visible note, not a block.
const PRIOR_NOTE_LABELS: Record<string, string> = {
  'taper-volume': 'the race week cuts too little volume',
  'taper-intensity': 'no intensity is left before the race',
  'race-eve': 'a long session just before the race',
  'back-to-back-hard': 'two hard days in a row',
  'ride-length': 'a ride well beyond your longest recent one',
  illness: 'intensity while you are ill or just getting back from it',
  'illness-fever': 'a session while you have a fever or are ill below the neck',
  'medical-clearance': 'a session before a doctor has checked your warning sign',
  'lasting-tiredness': 'intensity while you have been tired for days',
  'reduced-day': 'intensity on a day you said you have less to give',
  'stated-hours': 'the week holds more hours than you said you have',
}

/** An interval's target: watts on a ride with an FTP, else heart rate or pace (ai-trainer-ops#117). */
function intervalTarget(t: Translate, iv: { power?: number; heartRate?: number; pace?: string }): string {
  if (iv.power != null) return `${iv.power}W`
  if (iv.heartRate != null) return t('{n} bpm', { n: iv.heartRate })
  return iv.pace ?? '—'
}

function seconds(value: number): string {
  return value >= 60
    ? `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
    : `${value}s`
}

/** How a prescribed lift's intensity reads to the athlete (#714).
 *
 *  "RIR 2" rather than a weight, because the plan cannot know what a given
 *  weight will feel like on the day — leaving two reps in reserve is an
 *  instruction they can follow honestly when fresh and when tired. "@ 75 %"
 *  for a percentage-based block. The backend's persist gate keeps at most one of
 *  the two, so this never has to show both; an exercise with neither is a pure
 *  volume prescription ("3×12 push-ups") and renders without an intensity. */
function intensity(exercise: { rir?: number; percentE1rm?: number }): string | null {
  if (exercise.rir !== undefined && exercise.rir !== null) return `RIR ${exercise.rir}`
  if (exercise.percentE1rm !== undefined && exercise.percentE1rm !== null) {
    return `@ ${Math.round(exercise.percentE1rm)}%`
  }
  return null
}

const heading = 'text-sm font-semibold text-gray-900'
const th = 'py-1.5 text-left font-medium text-gray-500'

/** What the session asks of the athlete: targets, instructions, and the gain.
 *
 * Shared by the workout page and the dashboard hero (#623, #634) so the two
 * cannot describe the same ride differently.  Deliberately holds none of the
 * page's own concerns — logging, feedback, change history, chat — only the
 * part an athlete reads before going out.
 *
 * Plain sections in the card's own text since the owner's review of
 * 2026-10-24: no blue and amber boxes, no icons beside the headings. The hero
 * is a white card now, so the dark palette it needed is gone too. So is the
 * "Regenerate your plan" note on a day without coaching cues: its link led to
 * the dashboard, where the plan has rebuilt itself since ai-trainer-ops#206.
 */
export default function WorkoutDetails({
  day,
  showSummary = true,
}: {
  day: TrainingDay
  /** The hero renders a summary of its own, weather and all, so it turns this
   *  one off rather than stating the duration twice (#634). */
  showSummary?: boolean
}) {
  const t = useT()
  const planText = usePlanText()
  const language = useLanguage()

  const summary = [
    formatPlanDuration(day, language),
    day.targetPower && `${day.targetPower.low}–${day.targetPower.high}W`,
    day.targetHeartRate && t('{low}–{high} bpm', { low: day.targetHeartRate.low, high: day.targetHeartRate.high }),
  ].filter((part): part is string => Boolean(part))

  return (
    <>
      {showSummary && (
        <p className="mb-4 text-sm text-gray-500">
          {summary.map((part) => (
            <span key={part} className={dotted}>
              {part}
            </span>
          ))}
        </p>
      )}

      {showSummary && (
        <p className="text-sm leading-relaxed whitespace-pre-wrap text-gray-700">{planText(day.description)}</p>
      )}

      {/* The instructions for the ride, directly under the ride they belong to.
          They used to sit below a paragraph of rationale, which put the reading
          the athlete needs on the road behind the one they need once (#623). */}
      {day.keyFocusPoints && day.keyFocusPoints.length > 0 && (
        <div className="mt-4">
          <h3 className={heading}>{t('Key Focus Points')}</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-gray-700">
            {day.keyFocusPoints.map((point, i) => (
              <li key={i}>{planText(point)}</li>
            ))}
          </ul>
        </div>
      )}

      {/* "Why this workout" until #623.  The athlete is about to ride, not to
          audit the planner: what this earns them is the useful half. */}
      {day.workoutPurpose && (
        <div className="mt-4">
          <h3 className={heading}>{t('Goal of this workout')}</h3>
          <p className="mt-1 text-sm leading-relaxed text-gray-700">{planText(day.workoutPurpose)}</p>
        </div>
      )}

      {day.priorNotes?.length ? (
        <p className="mt-4 text-sm text-gray-500" data-testid="prior-note">
          {t('Planning rule not met:')} {day.priorNotes.map((key) => t(PRIOR_NOTE_LABELS[key] ?? key)).join('; ')}.
        </p>
      ) : null}

      {day.intervals && day.intervals.length > 0 && (
        <div className="mt-4">
          <h3 className={`${heading} mb-1`}>{t('Interval Breakdown')}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className={th}>#</th>
                  <th className={th}>{t('Duration')}</th>
                  <th className={th}>{t('Target')}</th>
                  <th className={th}>{t('Rest between')}</th>
                </tr>
              </thead>
              <tbody>
                {day.intervals.map((iv, i) => (
                  <tr key={i} className="border-b border-gray-100 last:border-0">
                    <td className="py-1.5 text-gray-500">{i + 1}</td>
                    <td className="py-1.5 text-gray-900">{seconds(iv.duration)}</td>
                    <td className="py-1.5 text-gray-900">{intervalTarget(t, iv)}</td>
                    <td className="py-1.5 text-gray-500">{seconds(iv.rest)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {day.strengthExercises && day.strengthExercises.length > 0 && (
        <div className="mt-4">
          <h3 className={`${heading} mb-1`}>{t('Lifts')}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className={th}>{t('Exercise')}</th>
                  <th className={th}>{t('Sets × Reps')}</th>
                  <th className={th}>{t('Intensity')}</th>
                </tr>
              </thead>
              <tbody>
                {day.strengthExercises.map((ex, i) => (
                  <tr key={`${ex.exercise}-${i}`} className="border-b border-gray-100 last:border-0">
                    <td className="py-1.5 capitalize text-gray-900">{planText(ex.exercise)}</td>
                    <td className="py-1.5 text-gray-900">
                      {ex.sets} × {ex.reps}
                    </td>
                    {/* An em dash rather than a blank cell: "no intensity
                        prescribed" is a statement, and an empty cell reads as a
                        value that failed to load. */}
                    <td className="py-1.5 text-gray-500">{intensity(ex) ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
