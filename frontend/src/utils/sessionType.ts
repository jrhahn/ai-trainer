import { Bike, Circle, Dumbbell, Flag, Gauge, Moon, Wind, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { TrainingDay } from '../store/useAppStore'

type WorkoutType = TrainingDay['workoutType']

interface TypeStyle {
  /** The full name. Used wherever there is room for it. */
  label: string
  /** Says the type without relying on colour (ai-trainer-ops#51.4). */
  Icon: LucideIcon
}

/** What a session type is called and what it looks like.
 *
 * Each type carries an icon. The week strip's chips show the session's own
 * title, and the icon is what says the type there (ai-trainer-ops#90). The
 * short names it used to show ("Base" for "Endurance") are gone: one day read
 * differently on a phone and a desktop. The icon also ends a colour-only
 * distinction (WCAG 1.4.1, ai-trainer-ops#51.4).
 *
 * The icons are grey. They used to run from cool to warm, one colour per type,
 * until the owner's review of 2026-10-24 asked for one accent on every page
 * ("kein Kunterbunt"). The shape says the type; the name says it in words.
 */
const TYPE_STYLES: Record<WorkoutType, TypeStyle> = {
  rest: { label: 'Rest', Icon: Moon },
  recovery: { label: 'Recovery', Icon: Wind },
  endurance: { label: 'Endurance', Icon: Bike },
  tempo: { label: 'Tempo', Icon: Gauge },
  intervals: { label: 'Intervals', Icon: Zap },
  strength: { label: 'Strength', Icon: Dumbbell },
  race: { label: 'Race', Icon: Flag },
}

const FALLBACK: TypeStyle = { label: 'Session', Icon: Circle }

/** Every type, taken from the table rather than listed again.
 *
 * `TYPE_STYLES` is a `Record<WorkoutType, …>`, so it cannot miss one — and a
 * test iterating this covers a type added to the union tomorrow, which a
 * hand-written array would silently skip.
 */
export const SESSION_TYPES = Object.keys(TYPE_STYLES) as WorkoutType[]

export function sessionTypeStyle(type: WorkoutType | undefined) {
  return (type && TYPE_STYLES[type]) || FALLBACK
}

/** The fallback is reachable from the API, so it is part of the contract. */
export const fallbackSessionTypeStyle = () => FALLBACK
