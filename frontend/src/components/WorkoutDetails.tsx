import { BarChart2, Clock, Heart, ListChecks, RefreshCw, Target, Zap } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'

function seconds(value: number): string {
  return value >= 60
    ? `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
    : `${value}s`
}

/** What the session asks of the athlete: targets, instructions, and the gain.
 *
 * Shared by the workout page and the dashboard's session overlay (#623) so the
 * two cannot describe the same ride differently.  Deliberately holds none of
 * the page's own concerns — logging, feedback, change history, chat — only the
 * part an athlete reads before going out.
 */
export default function WorkoutDetails({ day }: { day: TrainingDay }) {
  const navigate = useNavigate()

  return (
    <>
      <div className="flex flex-wrap gap-4 mb-4">
        <div className="flex items-center gap-1.5 text-sm text-gray-600">
          <Clock size={16} className="text-amber-500" />
          {formatPlanDuration(day)}
        </div>
        {day.targetPower && (
          <div className="flex items-center gap-1.5 text-sm text-gray-600">
            <Zap size={16} className="text-amber-500" />
            {day.targetPower.low}–{day.targetPower.high}W
          </div>
        )}
        {day.targetHeartRate && (
          <div className="flex items-center gap-1.5 text-sm text-gray-600">
            <Heart size={16} className="text-red-400" />
            {day.targetHeartRate.low}–{day.targetHeartRate.high} bpm
          </div>
        )}
      </div>

      <p className="text-gray-700 text-sm leading-relaxed whitespace-pre-wrap">{day.description}</p>

      {/* The instructions for the ride, directly under the ride they belong to.
          They used to sit below a paragraph of rationale, which put the reading
          the athlete needs on the road behind the one they need once (#623). */}
      {day.keyFocusPoints && day.keyFocusPoints.length > 0 && (
        <div className="mt-4 bg-blue-50 rounded-xl p-4">
          <h3 className="font-semibold text-sm text-blue-800 mb-1.5 flex items-center gap-1.5">
            <ListChecks size={14} />
            Key Focus Points
          </h3>
          <ul className="space-y-1">
            {day.keyFocusPoints.map((point, i) => (
              <li key={i} className="text-sm text-blue-900 flex items-start gap-2">
                <span className="mt-1 shrink-0 w-1.5 h-1.5 rounded-full bg-blue-400" />
                {point}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* "Why this workout" until #623.  The athlete is about to ride, not to
          audit the planner: what this earns them is the useful half. */}
      {day.workoutPurpose && (
        <div className="mt-3 bg-amber-50 rounded-xl p-4">
          <h3 className="font-semibold text-sm text-amber-800 mb-1.5 flex items-center gap-1.5">
            <Target size={14} />
            Goal of this workout
          </h3>
          <p className="text-sm text-amber-900 leading-relaxed">{day.workoutPurpose}</p>
        </div>
      )}

      {/* Nudge to regenerate if coaching details are missing */}
      {!day.workoutPurpose && day.workoutType !== 'rest' && (
        <div className="mt-4 flex items-start gap-3 bg-gray-50 rounded-xl p-4 border border-gray-200">
          <RefreshCw size={16} className="text-gray-400 mt-0.5 shrink-0" />
          <p className="text-xs text-gray-500 leading-relaxed">
            This workout was created before detailed coaching cues were added.{' '}
            <button
              onClick={() => navigate('/')}
              className="text-amber-600 hover:underline font-medium"
            >
              Regenerate your plan
            </button>{' '}
            to unlock goals and focus points.
          </p>
        </div>
      )}

      {day.intervals && day.intervals.length > 0 && (
        <div className="mt-4">
          <h3 className="font-semibold text-sm text-gray-800 mb-2 flex items-center gap-1.5">
            <BarChart2 size={15} />
            Interval Breakdown
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-1.5 text-gray-500 font-medium">#</th>
                  <th className="text-left py-1.5 text-gray-500 font-medium">Duration</th>
                  <th className="text-left py-1.5 text-gray-500 font-medium">Power</th>
                  <th className="text-left py-1.5 text-gray-500 font-medium">Rest</th>
                </tr>
              </thead>
              <tbody>
                {day.intervals.map((iv, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1.5 text-gray-400">{i + 1}</td>
                    <td className="py-1.5 text-gray-900 font-medium">{seconds(iv.duration)}</td>
                    <td className="py-1.5 text-gray-900 font-medium">{iv.power}W</td>
                    <td className="py-1.5 text-gray-500">{seconds(iv.rest)}</td>
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
