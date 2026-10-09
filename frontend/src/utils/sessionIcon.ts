/** Which icon stands for one planned session (#710, ai-trainer-ops#51.7).
 *
 * Lifted out of `TrainingCalendar` so the rule can be tested as a rule. It used
 * to be checked by looking for "🏃" in a calendar cell's `textContent`, which
 * only worked while the glyph *was* the implementation — and the whole point of
 * #51.7 is that it no longer is. An icon leaves no text behind, so the choice
 * has to be observable somewhere other than the rendered string.
 */

import { Bike, Dumbbell, Footprints, Timer } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { TrainingDay } from '../store/useAppStore'
import {
  PLAN_SPORT_CYCLING,
  PLAN_SPORT_RUNNING,
  PLAN_SPORT_STRENGTH,
  planSport,
} from './planSport'
import { sessionTypeStyle } from './sessionType'

const sportIcons: Record<string, LucideIcon> = {
  [PLAN_SPORT_CYCLING]: Bike,
  [PLAN_SPORT_RUNNING]: Footprints,
  [PLAN_SPORT_STRENGTH]: Dumbbell,
}

/** What to draw for a session in a sport we have no icon for.
 *
 * A stopwatch, deliberately: duration is all such a session reliably is, and it
 * names no sport. Falling back to the workout type's own icon would put a
 * bicycle on a planned swim, which is the exact confusion this function exists
 * to remove. Reachable through a stored sport outside `PLANNABLE_SPORTS` — the
 * persist gate keeps an athlete's own edit rather than relabelling it as
 * cycling.
 */
export const UNKNOWN_SPORT_ICON = Timer

/** The sport decides when the workout type cannot (#710).
 *
 * `endurance` and `strength` name a sport rather than an intent, so a planned
 * run showed the athlete a bicycle. Every other type — rest, intervals, tempo,
 * race, recovery — describes what the session is *for* in any sport, and keeps
 * its own.
 *
 * Those come from `sessionTypeStyle`, which is where the week strip gets its
 * icons too. The calendar used to keep a second table of its own, so the same
 * Thursday could be a flame in one place and a gauge in the other. The
 * *colours* are still separate: the calendar's `typeColors` is a different
 * palette from `sessionType.ts`, and reconciling them changes how the calendar
 * looks — not a decision to make inside an icon change.
 */
export function sessionIcon(session: TrainingDay): LucideIcon {
  if (session.workoutType === 'endurance' || session.workoutType === 'strength') {
    return sportIcons[planSport(session)] ?? UNKNOWN_SPORT_ICON
  }
  return sessionTypeStyle(session.workoutType).Icon
}
