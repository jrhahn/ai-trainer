import { Sprout } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'

/** Activities after which the backend's own models start reading the athlete
 * (`INSIGHT_MIN_ACTIVITIES`, `PREDICTION_MIN_ACTIVITIES` and the rest are 8). */
export const COLD_START_ACTIVITIES = 8

/** What a plan without rides was built from (ai-trainer-ops#89).
 *
 * A tester judges the app on the first plan, and without synced rides that plan
 * comes from four onboarding answers. Built from the profile and the activity
 * count only, so it cannot say anything the app does not know.
 */
export default function ColdStartNote() {
  const { profile, activities, loadFailed } = useAppStore(
    useShallow((s) => ({
      profile: s.userProfile,
      activities: s.rideMetricsHistory.length,
      loadFailed: Boolean(s.dataLoadWarning),
    }))
  )
  // A failed load leaves the ride history empty, and the note would then tell
  // an athlete with rides that nothing is known about them.
  if (!profile || loadFailed || activities >= COLD_START_ACTIVITIES) return null

  const goal =
    profile.trainingGoal === 'race'
      ? profile.raceDate
        ? `a race on ${parseLocalDate(profile.raceDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`
        : 'a race'
      : 'general fitness'
  const used = [
    goal,
    profile.weeklyHours != null && `${profile.weeklyHours} h a week`,
    profile.trainingDays?.length &&
      profile.trainingDays.map((d) => d[0].toUpperCase() + d.slice(1, 3)).join(', '),
    `${profile.fitnessLevel} level`,
    profile.currentFTP && `FTP ${profile.currentFTP} W`,
  ].filter(Boolean)
  const unknown = [!profile.currentFTP && 'your FTP', 'your recent training load', 'how you respond to sessions'].filter(
    Boolean
  )

  return (
    <section className="flex gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-gray-700">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
        <Sprout size={16} aria-hidden="true" />
      </span>
      <div className="space-y-1">
        <h2 className="font-semibold text-gray-900">Your plan starts from your answers</h2>
        <p>It used: {used.join(', ')}.</p>
        <p>It does not know yet: {unknown.join(', ')}.</p>
        {activities === 0 ? (
          <p>
            So the coach is told to keep the first week moderate: mostly easy endurance, at most one harder session.
            Every activity you sync or log replaces a guess with a measurement.
          </p>
        ) : (
          <p>
            {activities} of {COLD_START_ACTIVITIES} activities so far. Every one replaces a guess with a measurement.
          </p>
        )}
        <p className="text-gray-500">This note goes away after {COLD_START_ACTIVITIES} activities.</p>
      </div>
    </section>
  )
}
