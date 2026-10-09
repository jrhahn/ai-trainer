/** The 1–5 perceived-effort scale, in one place (ai-trainer-ops#51.5).
 *
 * It was written out twice: once in `WorkoutFeedbackForm`, which has a separate
 * wording for strength sessions, and once in `WorkoutPage`, which did not. So
 * logging a strength session at 2 showed "Controlled" in the form and
 * "Moderate" in the log directly afterwards — the same number, named two
 * different things on two consecutive screens.
 */

import type { TrainingDay } from '../store/useAppStore'

export type EffortLevel = 1 | 2 | 3 | 4 | 5

export const EFFORT_LEVELS: readonly EffortLevel[] = [1, 2, 3, 4, 5]

const RIDE_LABELS: Record<EffortLevel, string> = {
  1: 'Easy',
  2: 'Moderate',
  3: 'Hard',
  4: 'Very Hard',
  5: 'Max',
}

/** A strength session is not "moderate", it is controlled. Same scale, the
 *  vocabulary a lifter uses for it. */
const STRENGTH_LABELS: Record<EffortLevel, string> = {
  1: 'Easy',
  2: 'Controlled',
  3: 'Challenging',
  4: 'Very Hard',
  5: 'Max',
}

export function effortLabelsFor(
  day: Pick<TrainingDay, 'workoutType'> | undefined
): Record<EffortLevel, string> {
  return day?.workoutType === 'strength' ? STRENGTH_LABELS : RIDE_LABELS
}

/** What the picker and the log should both call a level. */
export function effortLabel(
  day: Pick<TrainingDay, 'workoutType'> | undefined,
  level: number
): string {
  return effortLabelsFor(day)[level as EffortLevel] ?? String(level)
}
