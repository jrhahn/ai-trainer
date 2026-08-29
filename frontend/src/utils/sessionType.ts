import type { TrainingDay } from '../store/useAppStore'

type WorkoutType = TrainingDay['workoutType']

/** One colour per session type, so a hard day looks hard everywhere it appears.
 *
 * Before this the pills were arbitrary greys with `intervals` in solid black,
 * which made a threshold day and a race look identical and an endurance day
 * indistinguishable from a rest day.  Intensity now maps to warmth: rest and
 * recovery cool, endurance neutral, tempo upwards amber to red.
 */
const TYPE_STYLES: Record<WorkoutType, { pill: string; dot: string; label: string }> = {
  rest: { pill: 'bg-slate-100 text-slate-500', dot: 'bg-slate-300', label: 'Rest' },
  recovery: { pill: 'bg-sky-50 text-sky-700', dot: 'bg-sky-400', label: 'Recovery' },
  endurance: { pill: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'Endurance' },
  tempo: { pill: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500', label: 'Tempo' },
  intervals: { pill: 'bg-orange-100 text-orange-700', dot: 'bg-orange-500', label: 'Intervals' },
  strength: { pill: 'bg-violet-50 text-violet-700', dot: 'bg-violet-400', label: 'Strength' },
  race: { pill: 'bg-red-100 text-red-700', dot: 'bg-red-500', label: 'Race' },
}

const FALLBACK = { pill: 'bg-slate-100 text-slate-600', dot: 'bg-slate-300', label: 'Session' }

export function sessionTypeStyle(type: WorkoutType | undefined) {
  return (type && TYPE_STYLES[type]) || FALLBACK
}

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
