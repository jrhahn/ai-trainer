import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle2, Clock, Heart, Zap } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionTypeStyleOnDark } from '../utils/sessionType'
import WeatherBadge from './WeatherBadge'

/** Today's session, given the weight it actually has.
 *
 * It used to be half of one grey line ("Today: Zone 2 endurance · Tomorrow:
 * Rest") above a chat log that filled the screen — the one thing the athlete
 * opens the app for, rendered smaller than everything around it.
 */
export default function TodaySessionHero({
  day,
  loggedToday = false,
}: {
  day: TrainingDay | undefined
  /** A ride exists for today even if the plan day was never hand-ticked.  The
   *  old status strip derived "completed" from the ride data, and dropping that
   *  would have quietly regressed two-a-days and unticked-but-ridden days. */
  loggedToday?: boolean
}) {
  const forecast = useAppStore(useShallow((s) => (day ? s.weatherForecast[day.date] : undefined)))

  if (!day) {
    return (
      <section className="rounded-2xl border border-slate-200 bg-white px-5 py-6 text-center">
        <p className="text-sm font-semibold text-gray-900">No session planned for today</p>
        <p className="mt-1 text-sm text-gray-500">
          Ask your coach below and it will put one in.
        </p>
      </section>
    )
  }

  const isRest = day.workoutType === 'rest'
  const isDone = day.completed || loggedToday

  return (
    <section className="relative overflow-hidden rounded-2xl bg-[#0f1116] text-white shadow-lg">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            'radial-gradient(36rem 20rem at 8% -30%, rgba(245,158,11,0.22), transparent 60%), radial-gradient(28rem 18rem at 96% 10%, rgba(20,184,166,0.14), transparent 62%)',
        }}
      />
      <div className="relative px-5 py-5 sm:px-7 sm:py-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">
            Today
          </span>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${sessionTypeStyleOnDark(
              day.workoutType
            )}`}
          >
            {day.workoutType}
          </span>
          {isDone && (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-400/20 px-2.5 py-0.5 text-xs font-semibold text-emerald-200">
              <CheckCircle2 size={12} aria-hidden="true" />
              Done
            </span>
          )}
        </div>

        <h2 className="mt-3 text-2xl font-black leading-tight tracking-tight sm:text-3xl">
          {day.title}
        </h2>

        {day.description && (
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300 sm:text-[15px]">
            {day.description}
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          {!isRest && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-white">
              <Clock size={15} className="text-slate-400" aria-hidden="true" />
              {formatPlanDuration(day)}
            </span>
          )}
          {day.targetPower && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-white">
              <Zap size={15} className="text-amber-300" aria-hidden="true" />
              {day.targetPower.low}–{day.targetPower.high} W
            </span>
          )}
          {day.targetHeartRate && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-white">
              <Heart size={15} className="text-rose-300" aria-hidden="true" />
              {day.targetHeartRate.low}–{day.targetHeartRate.high} bpm
            </span>
          )}
          <WeatherBadge forecast={forecast} className="text-slate-300" />
        </div>

        <Link
          to={`/workout/${day.date}`}
          className="mt-5 inline-flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 text-sm font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
        >
          {isDone ? 'Review the session' : isRest ? 'See the day' : 'Open the session'}
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </div>
    </section>
  )
}
