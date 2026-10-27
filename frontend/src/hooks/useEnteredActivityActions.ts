import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, WorkoutFeedback } from '../store/useAppStore'
import type { EditedActivity } from '../components/LogSessionDialog'
import { enteredActivitySlot } from '../utils/enteredActivity'
import { deleteManualActivity, fetchMetricsHistory, fetchRideMetricsHistory, fetchWorkoutLogs } from '../services/user'
import { useT } from '../i18n'

/** The form values of an entered activity, read from its log (#47).
 *
 * The effort and the note live on the log, not on the load row, so an edit is
 * opened from here. Empty strings for what the athlete never said, so a field
 * comes back blank rather than claiming 0 km. */
function editedActivityFrom(date: string, slot: number, log: WorkoutFeedback): EditedActivity {
  const sport = (['cycling', 'running', 'strength'] as const).find((s) => s === log.sport) ?? 'cycling'
  return {
    date,
    slot,
    sport,
    durationMinutes: log.actualDurationMinutes,
    // A log may say 0, "not reported", since an import writes that
    // (ai-trainer-ops#311). An entry made here has 1-5; the form needs one.
    perceivedEffort: log.perceivedEffort || 3,
    distanceKm: log.distanceKm != null ? String(log.distanceKm) : '',
    averageHeartRate: log.averageHeartRate != null ? String(log.averageHeartRate) : '',
    normalizedPower: log.normalizedPower != null ? String(log.normalizedPower) : '',
    notes: log.notes ?? '',
  }
}

/** Edit and delete for an activity the athlete entered with "Add activity".
 *
 * Shared by the dashboard's activity list and the day page (ai-trainer-ops#207),
 * so the two open the same form and make the same calls. */
export function useEnteredActivityActions() {
  const authToken = useAppStore((s) => s.authToken)
  const setRideMetricsHistory = useAppStore((s) => s.setRideMetricsHistory)
  const setMetricsHistory = useAppStore((s) => s.setMetricsHistory)
  const t = useT()

  /** The form values for `ride`, or `null` when it is not an entered activity
   *  or its log could not be read (the athlete was told why). */
  const open = async (ride: RideMetricPoint): Promise<EditedActivity | null> => {
    const slot = enteredActivitySlot(ride)
    if (!authToken || slot == null) return null
    try {
      const log = (await fetchWorkoutLogs(authToken))[`${ride.activityDate}#${slot}`]
      if (!log) throw new Error('This activity could not be found any more.')
      return editedActivityFrom(ride.activityDate, slot, log)
    } catch (e) {
      window.alert(t(e instanceof Error ? e.message : 'The activity could not be opened.'))
      return null
    }
  }

  /** Delete `ride` after asking, then reload the rides. True when it is gone. */
  const remove = async (ride: RideMetricPoint): Promise<boolean> => {
    const slot = enteredActivitySlot(ride)
    if (!authToken || slot == null) return false
    if (!window.confirm(t('Delete this activity? Its training load goes with it.'))) return false
    try {
      await deleteManualActivity(authToken, ride.activityDate, slot)
      const [rides, metrics] = await Promise.all([
        fetchRideMetricsHistory(authToken),
        fetchMetricsHistory(authToken),
      ])
      setRideMetricsHistory(rides)
      setMetricsHistory(metrics)
      return true
    } catch (e) {
      window.alert(t(e instanceof Error ? e.message : 'The activity could not be deleted.'))
      return false
    }
  }

  return { open, remove }
}
