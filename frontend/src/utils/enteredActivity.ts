import type { RideMetricPoint } from '../store/useAppStore'

/** The source of a session the athlete logged by hand (`logged_sessions`). */
export const LOGGED_SOURCE = 'logged'

/** Slots from here up are sessions entered outside the plan (ai-trainer-ops#47). */
export const UNPLANNED_SLOT_BASE = 100

/**
 * The slot of a session the athlete entered with "Add activity", or `null`.
 *
 * Its id is `date#slot`. Only the unplanned range counts: a planned session's
 * hand-written log is also `logged`, but it *is* the plan's session, so it can
 * tick it and cannot be deleted from the activity list.
 */
export function enteredActivitySlot(ride: RideMetricPoint): number | null {
  if (ride.activitySource !== LOGGED_SOURCE) return null
  const slot = Number((ride.externalActivityId ?? '').split('#')[1])
  return Number.isInteger(slot) && slot >= UNPLANNED_SLOT_BASE ? slot : null
}

export function isEnteredActivity(ride: RideMetricPoint): boolean {
  return enteredActivitySlot(ride) != null
}
