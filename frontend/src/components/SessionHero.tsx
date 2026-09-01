import { CheckCircle2, Clock, Heart, Zap } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionTypeStyleOnDark } from '../utils/sessionType'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import WeatherBadge from './WeatherBadge'
import WorkoutDetails from './WorkoutDetails'

/** The session the athlete is looking at, given the weight it actually has.
 *
 * It used to be half of one grey line ("Today: Zone 2 endurance · Tomorrow:
 * Rest") above a chat log that filled the screen — the one thing the athlete
 * opens the app for, rendered smaller than everything around it.
 *
 * Since #634 it is also where the week strip reports: picking a day re-renders
 * this card instead of navigating away, and the session is shown whole rather
 * than behind a "Show details" button.  Was `TodaySessionHero` until then, which
 * stopped being true the moment it could show Friday.
 */
export default function SessionHero({
  day,
  date,
  logged = false,
}: {
  day: TrainingDay | undefined
  /** The selected day, needed even when the plan has nothing for it — an empty
   *  Thursday still has to say which day it is empty for. */
  date: string
  /** A ride exists for this date even if the plan day was never hand-ticked.
   *  The old status strip derived "completed" from the ride data, and dropping
   *  that would have quietly regressed two-a-days and unticked-but-ridden days. */
  logged?: boolean
}) {
  const forecast = useAppStore(useShallow((s) => s.weatherForecast[date]))
  const isToday = date === formatLocalDate(new Date())
  // "Today" while it is, the weekday otherwise.  A bare date would make the
  // athlete do the conversion the strip they just clicked already did for them.
  const when = isToday
    ? 'Today'
    : parseLocalDate(date).toLocaleDateString(undefined, { weekday: 'long' })

  if (!day) {
    return (
      <section className="rounded-2xl border border-slate-200 bg-white px-5 py-6 text-center">
        <p className="text-sm font-semibold text-gray-900">
          No session planned for {isToday ? 'today' : when}
        </p>
        <p className="mt-1 text-sm text-gray-500">Ask your coach below and it will put one in.</p>
      </section>
    )
  }

  const isRest = day.workoutType === 'rest'
  const isDone = day.completed || logged

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
            {when}
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

        {/* What the athlete rides, in the card they are already looking at.  It
            sat behind a "Show details" button until #634 — one more click for
            the only content on the card they need on the road.  The summary row
            is suppressed because the block above already is one. */}
        {/* No way out at the bottom: the card is the session, and an athlete
            reading it is not looking for somewhere else to go.  The workout page
            is still reached from the calendar behind "Show more". */}
        <div className="mt-5">
          <WorkoutDetails day={day} variant="dark" showSummary={false} />
        </div>
      </div>
    </section>
  )
}
