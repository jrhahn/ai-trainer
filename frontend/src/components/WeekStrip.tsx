import { Check } from 'lucide-react'
import { addDays } from 'date-fns'
import type { TrainingDay } from '../store/useAppStore'
import { sessionsForDate } from '../utils/planSessions'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { sessionTypeStyle } from '../utils/sessionType'

/** The training week as seven chips instead of a list of three rows.
 *
 * The old activity list gave a third of the width to the date and truncated the
 * session name — on mobile "4 x 8 min thresh…". A week is small enough to show
 * whole, and seeing it whole is the point: where the hard days sit, what is
 * already done, what is coming.
 *
 * The seven days are a *rolling* window centred on today, not Monday–Sunday
 * (#672). A calendar week answers "what did I just ride, what is next" well
 * exactly once: on a Sunday the old strip showed six days of history and no
 * upcoming session at all, because tomorrow belonged to next week. Centring
 * costs nothing and makes the answer the same on every weekday.
 *
 * The chips used to link to `/workout/:date`, which answered "what is Wednesday?"
 * by throwing away the dashboard. Since #634 they select instead, and the hero
 * above re-renders — the page you are on is already the right shape for the
 * answer.
 */
/** How far back and forward the strip reaches.  Three either side keeps today
 *  in the middle column of a seven-wide grid, which is what makes the window
 *  legible without a marker. */
const DAYS_EITHER_SIDE = 3
const DAYS_SHOWN = DAYS_EITHER_SIDE * 2 + 1

export default function WeekStrip({
  plan,
  selectedDate,
  onSelect,
  onShowMore,
}: {
  plan: TrainingDay[]
  /** The day the hero is showing.  Distinct from today: today stays the week's
   *  reference point even while you are reading Friday. */
  selectedDate: string
  onSelect: (date: string) => void
  /** Opens the month calendar.  The week is the daily read; the block beyond it
   *  is a question the athlete asks occasionally, so it lives behind this. */
  onShowMore?: () => void
}) {
  const now = new Date()
  const today = formatLocalDate(now)

  // Not a Map keyed by date: that keeps only the last entry per date, which is
  // the same session-swallowing bug as `plan.find()` in another shape (#645).
  const week = Array.from({ length: DAYS_SHOWN }, (_, index) => {
    const date = formatLocalDate(addDays(now, index - DAYS_EITHER_SIDE))
    return { date, sessions: sessionsForDate(plan, date) }
  })

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        {/* Not "This week": the window is Thu–Wed on a Sunday, and a heading
            that claims the calendar week would be lying about it (#672). */}
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">Your week</h2>
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
        {week.map(({ date, sessions }) => {
          const isToday = date === today
          const isSelected = date === selectedDate
          const styles = sessions.map((session) => sessionTypeStyle(session.workoutType))
          const weekday = parseLocalDate(date).toLocaleDateString(undefined, { weekday: 'short' })
          // The chip reads "Tu" over a coloured dot over "Rest", which is thin
          // to listen to.  Spell out what the eye gets from the layout — and on
          // a two-a-day name both sessions, since the visible text cannot.
          const label = `${parseLocalDate(date).toLocaleDateString(undefined, {
            weekday: 'long',
          })} — ${styles.length ? styles.map((s) => s.label).join(', ') : 'nothing planned'}`

          // Selection is the loud state; today is a quiet one that survives
          // underneath it, so the week keeps its reference point either way.
          const chrome = isSelected
            ? 'border-amber-500 bg-amber-50 ring-2 ring-amber-200'
            : isToday
              ? 'border-amber-300 bg-amber-50/40 hover:border-amber-400'
              : 'border-slate-200 bg-white hover:border-slate-300'

          return (
            <button
              key={date}
              type="button"
              onClick={() => onSelect(date)}
              aria-label={label}
              aria-pressed={isSelected}
              aria-current={isToday ? 'date' : undefined}
              className={`flex min-h-[5.5rem] flex-col items-center rounded-xl border px-1 py-2 text-center transition-colors ${chrome}`}
            >
              <span
                className={`text-[11px] font-semibold uppercase ${
                  isToday ? 'text-amber-600' : 'text-gray-400'
                }`}
              >
                {weekday.slice(0, 2)}
              </span>
              {/* One dot per session, so a two-a-day is visible at a glance
                  rather than only in the label below (#645). */}
              <span className="mt-1.5 flex items-center gap-0.5">
                {styles.length === 0 ? (
                  <span className="h-2 w-2 rounded-full bg-slate-200" />
                ) : (
                  styles.map((s, i) => (
                    <span key={i} className={`h-2 w-2 rounded-full ${s.dot}`} />
                  ))
                )}
              </span>
              {/* Seven columns leave ~40px on a phone, which "Endurance" overruns
                  into its neighbour. Clip it there and spell it out from sm up.
                  Two session names never fit, so say how many instead. */}
              <span className="mt-1.5 w-full truncate px-0.5 text-[10px] font-medium leading-tight text-gray-600 sm:text-[11px]">
                {sessions.length === 0
                  ? '—'
                  : sessions.length === 1
                    ? styles[0].label
                    : `${sessions.length} sessions`}
              </span>
              {sessions.length > 0 && sessions.every((s) => s.completed) && (
                <Check size={12} className="mt-1 text-emerald-500" aria-hidden="true" />
              )}
            </button>
          )
        })}
      </div>
    </section>
  )
}
