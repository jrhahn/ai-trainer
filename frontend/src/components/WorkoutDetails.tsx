import { BarChart2, Clock, Heart, ListChecks, RefreshCw, Target, Zap } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'

function seconds(value: number): string {
  return value >= 60
    ? `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
    : `${value}s`
}

/** Where this renders.  The workout page is a light document; the dashboard hero
 *  is the dark card at the top of the page (#634).  Same content either way —
 *  splitting it into two components is what would let the two drift. */
export type WorkoutDetailsVariant = 'light' | 'dark'

const PALETTE: Record<WorkoutDetailsVariant, Record<string, string>> = {
  light: {
    meta: 'text-gray-600',
    metaIcon: 'text-amber-500',
    heartIcon: 'text-red-400',
    body: 'text-gray-700',
    focusBox: 'bg-blue-50',
    focusHeading: 'text-blue-800',
    focusText: 'text-blue-900',
    focusDot: 'bg-blue-400',
    goalBox: 'bg-amber-50',
    goalHeading: 'text-amber-800',
    goalText: 'text-amber-900',
    nudgeBox: 'bg-gray-50 border border-gray-200',
    nudgeIcon: 'text-gray-400',
    nudgeText: 'text-gray-500',
    nudgeLink: 'text-amber-600',
    tableHeading: 'text-gray-800',
    tableLabel: 'text-gray-500',
    tableRow: 'border-b last:border-0',
    tableHead: 'border-b',
    tableIndex: 'text-gray-400',
    tableValue: 'text-gray-900',
    tableRest: 'text-gray-500',
  },
  // Tuned against the hero's own palette: amber accent, teal secondary, slate
  // body text on #0f1116.  Translucent panels rather than solid fills, so the
  // card's gradient still reads through them.
  dark: {
    meta: 'text-slate-300',
    metaIcon: 'text-amber-300',
    heartIcon: 'text-rose-300',
    body: 'text-slate-300',
    focusBox: 'bg-white/[0.06] ring-1 ring-inset ring-white/10',
    focusHeading: 'text-teal-300',
    focusText: 'text-slate-200',
    focusDot: 'bg-teal-400',
    goalBox: 'bg-amber-400/10 ring-1 ring-inset ring-amber-400/20',
    goalHeading: 'text-amber-300',
    goalText: 'text-amber-100/90',
    nudgeBox: 'bg-white/[0.06] ring-1 ring-inset ring-white/10',
    nudgeIcon: 'text-slate-500',
    nudgeText: 'text-slate-400',
    nudgeLink: 'text-amber-300',
    tableHeading: 'text-slate-200',
    tableLabel: 'text-slate-400',
    tableRow: 'border-b border-white/10 last:border-0',
    tableHead: 'border-b border-white/15',
    tableIndex: 'text-slate-500',
    tableValue: 'text-white',
    tableRest: 'text-slate-400',
  },
}

/** What the session asks of the athlete: targets, instructions, and the gain.
 *
 * Shared by the workout page and the dashboard hero (#623, #634) so the two
 * cannot describe the same ride differently.  Deliberately holds none of the
 * page's own concerns — logging, feedback, change history, chat — only the
 * part an athlete reads before going out.
 */
export default function WorkoutDetails({
  day,
  variant = 'light',
  showSummary = true,
}: {
  day: TrainingDay
  variant?: WorkoutDetailsVariant
  /** The hero renders a larger summary of its own, weather badge and all, so it
   *  turns this one off rather than stating the duration twice (#634). */
  showSummary?: boolean
}) {
  const navigate = useNavigate()
  const c = PALETTE[variant]

  return (
    <>
      {showSummary && (
        <div className="flex flex-wrap gap-4 mb-4">
          <div className={`flex items-center gap-1.5 text-sm ${c.meta}`}>
            <Clock size={16} className={c.metaIcon} />
            {formatPlanDuration(day)}
          </div>
          {day.targetPower && (
            <div className={`flex items-center gap-1.5 text-sm ${c.meta}`}>
              <Zap size={16} className={c.metaIcon} />
              {day.targetPower.low}–{day.targetPower.high}W
            </div>
          )}
          {day.targetHeartRate && (
            <div className={`flex items-center gap-1.5 text-sm ${c.meta}`}>
              <Heart size={16} className={c.heartIcon} />
              {day.targetHeartRate.low}–{day.targetHeartRate.high} bpm
            </div>
          )}
        </div>
      )}

      {showSummary && (
        <p className={`text-sm leading-relaxed whitespace-pre-wrap ${c.body}`}>{day.description}</p>
      )}

      {/* The instructions for the ride, directly under the ride they belong to.
          They used to sit below a paragraph of rationale, which put the reading
          the athlete needs on the road behind the one they need once (#623). */}
      {day.keyFocusPoints && day.keyFocusPoints.length > 0 && (
        <div className={`mt-4 rounded-xl p-4 ${c.focusBox}`}>
          <h3 className={`font-semibold text-sm mb-1.5 flex items-center gap-1.5 ${c.focusHeading}`}>
            <ListChecks size={14} />
            Key Focus Points
          </h3>
          <ul className="space-y-1">
            {day.keyFocusPoints.map((point, i) => (
              <li key={i} className={`text-sm flex items-start gap-2 ${c.focusText}`}>
                <span className={`mt-1 shrink-0 w-1.5 h-1.5 rounded-full ${c.focusDot}`} />
                {point}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* "Why this workout" until #623.  The athlete is about to ride, not to
          audit the planner: what this earns them is the useful half. */}
      {day.workoutPurpose && (
        <div className={`mt-3 rounded-xl p-4 ${c.goalBox}`}>
          <h3 className={`font-semibold text-sm mb-1.5 flex items-center gap-1.5 ${c.goalHeading}`}>
            <Target size={14} />
            Goal of this workout
          </h3>
          <p className={`text-sm leading-relaxed ${c.goalText}`}>{day.workoutPurpose}</p>
        </div>
      )}

      {/* Nudge to regenerate if coaching details are missing */}
      {!day.workoutPurpose && day.workoutType !== 'rest' && (
        <div className={`mt-4 flex items-start gap-3 rounded-xl p-4 ${c.nudgeBox}`}>
          <RefreshCw size={16} className={`mt-0.5 shrink-0 ${c.nudgeIcon}`} />
          <p className={`text-xs leading-relaxed ${c.nudgeText}`}>
            This workout was created before detailed coaching cues were added.{' '}
            <button
              onClick={() => navigate('/')}
              className={`hover:underline font-medium ${c.nudgeLink}`}
            >
              Regenerate your plan
            </button>{' '}
            to unlock goals and focus points.
          </p>
        </div>
      )}

      {day.intervals && day.intervals.length > 0 && (
        <div className="mt-4">
          <h3 className={`font-semibold text-sm mb-2 flex items-center gap-1.5 ${c.tableHeading}`}>
            <BarChart2 size={15} />
            Interval Breakdown
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className={c.tableHead}>
                  <th className={`text-left py-1.5 font-medium ${c.tableLabel}`}>#</th>
                  <th className={`text-left py-1.5 font-medium ${c.tableLabel}`}>Duration</th>
                  <th className={`text-left py-1.5 font-medium ${c.tableLabel}`}>Power</th>
                  <th className={`text-left py-1.5 font-medium ${c.tableLabel}`}>Rest</th>
                </tr>
              </thead>
              <tbody>
                {day.intervals.map((iv, i) => (
                  <tr key={i} className={c.tableRow}>
                    <td className={`py-1.5 ${c.tableIndex}`}>{i + 1}</td>
                    <td className={`py-1.5 font-medium ${c.tableValue}`}>{seconds(iv.duration)}</td>
                    <td className={`py-1.5 font-medium ${c.tableValue}`}>{iv.power}W</td>
                    <td className={`py-1.5 ${c.tableRest}`}>{seconds(iv.rest)}</td>
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
