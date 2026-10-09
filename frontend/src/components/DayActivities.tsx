import { Clock } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { isEnteredActivity } from '../utils/enteredActivity'

/**
 * What the athlete actually did on a day, whatever the plan said
 * (ai-trainer-ops#47, #48).
 *
 * The day view used to show the plan's sessions only. A ride on a rest day, a
 * run entered with "Add activity", an uploaded file on a day the plan never
 * covered — none of them appeared on the day they happened, and the upload
 * result had nowhere to point. Read from the same list the dashboard's recent
 * activities use, so the two never disagree about what a day holds.
 */

function durationLabel(seconds: number | undefined): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m} min`
}

function identity(ride: RideMetricPoint): string {
  return ride.externalActivityId ? `external:${ride.externalActivityId}` : `strava:${ride.stravaActivityId}`
}

export default function DayActivities({ date }: { date: string }) {
  const rides = useAppStore((s) => s.rideMetricsHistory)
  const seen = new Set<string>()
  const onDay = rides.filter((ride) => {
    if (ride.activityDate !== date) return false
    const key = identity(ride)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  if (onDay.length === 0) return null

  return (
    <section className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h2 className="text-sm font-semibold text-gray-800 mb-3">What you did this day</h2>
      <ul className="space-y-1.5">
        {onDay.map((ride) => (
          <li key={identity(ride)} className="flex items-center gap-2 text-sm">
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold capitalize text-gray-700">
              {ride.sportType.toLowerCase().replace(/_/g, ' ')}
            </span>
            <span className="flex-1 truncate text-gray-700">
              {isEnteredActivity(ride) ? 'Entered by you' : (ride.activityName ?? 'Activity')}
            </span>
            {ride.durationSeconds ? (
              <span className="flex items-center gap-1 text-xs text-gray-500">
                <Clock size={11} aria-hidden="true" />
                {durationLabel(ride.durationSeconds)}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
