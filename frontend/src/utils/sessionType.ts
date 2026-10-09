import { Bike, Circle, Dumbbell, Flag, Gauge, Moon, Wind, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { TrainingDay } from '../store/useAppStore'

type WorkoutType = TrainingDay['workoutType']

interface TypeStyle {
  /** The full name. Used wherever there is room for it. */
  label: string
  /** The name for a column 36 px wide — see `SHORT_LABEL_BUDGET`. */
  short: string
  /** Says the type without relying on colour (ai-trainer-ops#51.4). */
  Icon: LucideIcon
  /** The type's place on the warmth scale, as a colour for the glyph. */
  iconColor: string
}

/** What a session type is called and what it looks like.
 *
 * One colour per type, so a hard day looks hard everywhere it appears: the
 * pills used to be arbitrary greys with `intervals` in solid black, which made
 * a threshold day and a race identical and an endurance day indistinguishable
 * from a rest day. Intensity maps to warmth — rest and recovery cool, endurance
 * neutral, tempo upwards amber to red.
 *
 * Each type carries an icon and a short name, because a seven-wide grid on a
 * 390 px phone leaves 36 px per column and "Endurance" is laid out at 58 —
 * measured, and it was being clipped to "Endur…" (ai-trainer-ops#51.4). The
 * icon also ends a colour-only distinction (WCAG 1.4.1): the dot it replaced
 * said nothing about whether tomorrow is a race to an athlete who cannot tell
 * green from red.
 *
 * The `pill` and `dot` class names that used to live here are gone. `dot` was
 * the week strip's, which now draws an icon instead; `pill` had no caller left
 * at all. The hero keeps its own scale, in `sessionTypeStyleOnDark` below.
 */
const TYPE_STYLES: Record<WorkoutType, TypeStyle> = {
  rest: {
    label: 'Rest',
    short: 'Rest',
    Icon: Moon,
    iconColor: 'text-slate-400',
  },
  recovery: {
    label: 'Recovery',
    short: 'Easy',
    Icon: Wind,
    iconColor: 'text-sky-500',
  },
  endurance: {
    label: 'Endurance',
    short: 'Base',
    Icon: Bike,
    iconColor: 'text-emerald-600',
  },
  tempo: {
    label: 'Tempo',
    short: 'Tempo',
    Icon: Gauge,
    iconColor: 'text-amber-600',
  },
  intervals: {
    label: 'Intervals',
    short: 'Reps',
    Icon: Zap,
    iconColor: 'text-orange-600',
  },
  strength: {
    label: 'Strength',
    short: 'Lift',
    Icon: Dumbbell,
    iconColor: 'text-violet-500',
  },
  race: {
    label: 'Race',
    short: 'Race',
    Icon: Flag,
    iconColor: 'text-red-600',
  },
}

const FALLBACK: TypeStyle = {
  label: 'Session',
  short: 'Other',
  Icon: Circle,
  iconColor: 'text-slate-400',
}

/** What fits in a week-strip column at 390 px.
 *
 * Measured, not guessed: the label renders at 10 px in a 36 px column, where
 * "Endurance" came to 58 px — about 6.4 px per character, so five characters is
 * the budget and nine was never going to work.
 *
 * Exported because a test asserts every `short` is within it. The seven short
 * names are a vocabulary choice and changing them is a one-line edit here; what
 * must not change is that they fit.
 */
export const SHORT_LABEL_BUDGET = 5

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

/** Same scale on the dark hero card, where the light pills would disappear. */
const ON_DARK: Record<WorkoutType, string> = {
  rest: 'bg-white/10 text-slate-200',
  recovery: 'bg-sky-400/20 text-sky-200',
  endurance: 'bg-emerald-400/20 text-emerald-200',
  tempo: 'bg-amber-400/20 text-amber-200',
  intervals: 'bg-orange-400/25 text-orange-200',
  strength: 'bg-violet-400/20 text-violet-200',
  race: 'bg-red-400/25 text-red-200',
}

export function sessionTypeStyleOnDark(type: WorkoutType | undefined) {
  return (type && ON_DARK[type]) || 'bg-white/10 text-slate-200'
}

export function isRestDay(day: Pick<TrainingDay, 'workoutType'> | undefined): boolean {
  return day?.workoutType === 'rest'
}
