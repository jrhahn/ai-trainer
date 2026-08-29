import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'
import { addDays, startOfWeek } from 'date-fns'
import type { TrainingDay } from '../store/useAppStore'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { sessionTypeStyle } from '../utils/sessionType'

/** The training week as seven chips instead of a list of three rows.
 *
 * The old activity list gave a third of the width to the date and truncated the
 * session name — on mobile "4 x 8 min thresh…". A week is small enough to show
 * whole, and seeing it whole is the point: where the hard days sit, what is
 * already done, what is coming.
 */
export default function WeekStrip({
  plan,
  onShowMore,
}: {
  plan: TrainingDay[]
  /** Opens the month calendar.  The week is the daily read; the block beyond it
   *  is a question the athlete asks occasionally, so it lives behind this. */
  onShowMore?: () => void
}) {
  const today = formatLocalDate(new Date())
  const monday = startOfWeek(new Date(), { weekStartsOn: 1 })
  const byDate = new Map(plan.map((day) => [day.date, day]))

  const week = Array.from({ length: 7 }, (_, index) => {
    const date = formatLocalDate(addDays(monday, index))
    return { date, day: byDate.get(date) }
  })

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">This week</h2>
        {onShowMore && (
          <button
            type="button"
            onClick={onShowMore}
            className="text-xs font-semibold text-amber-600 transition-colors hover:text-amber-700"
          >
            Show more
          </button>
        )}
      </div>
      <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
        {week.map(({ date, day }) => {
          const isToday = date === today
          const style = sessionTypeStyle(day?.workoutType)
          const weekday = parseLocalDate(date).toLocaleDateString(undefined, { weekday: 'short' })

          const content = (
            <>
              <span
                className={`text-[11px] font-semibold uppercase ${
                  isToday ? 'text-amber-600' : 'text-gray-400'
                }`}
              >
                {weekday.slice(0, 2)}
              </span>
              <span className={`mt-1.5 h-2 w-2 rounded-full ${day ? style.dot : 'bg-slate-200'}`} />
              {/* Seven columns leave ~40px on a phone, which "Endurance" overruns
                  into its neighbour. Clip it there and spell it out from sm up. */}
              <span className="mt-1.5 w-full truncate px-0.5 text-[10px] font-medium leading-tight text-gray-600 sm:text-[11px]">
                {day ? style.label : '—'}
              </span>
              {day?.completed && (
                <Check size={12} className="mt-1 text-emerald-500" aria-hidden="true" />
              )}
            </>
          )

          const className = `flex min-h-[5.5rem] flex-col items-center rounded-xl border px-1 py-2 text-center transition-colors ${
            isToday
              ? 'border-amber-400 bg-amber-50/60'
              : 'border-slate-200 bg-white hover:border-slate-300'
          }`

          return day ? (
            <Link key={date} to={`/workout/${date}`} className={className} aria-current={isToday ? 'date' : undefined}>
              {content}
            </Link>
          ) : (
            <div key={date} className={className}>
              {content}
            </div>
          )
        })}
      </div>
    </section>
  )
}
