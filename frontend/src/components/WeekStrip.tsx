import { Check } from 'lucide-react'
import { addDays } from 'date-fns'
import type { TrainingDay } from '../store/useAppStore'
import { sessionsForDate } from '../utils/planSessions'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { sessionTypeStyle } from '../utils/sessionType'
import { formatPlanDuration, planDurationRange } from '../utils/planDuration'
import { localeTag, useLanguage, useT } from '../i18n'

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
  const t = useT()
  const language = useLanguage()
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
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">{t('Your week')}</h2>
        {onShowMore && (
          <button
            type="button"
            onClick={onShowMore}
            className="text-xs font-semibold text-amber-600 transition-colors hover:text-amber-700"
          >
            {t('Show more')}
          </button>
        )}
      </div>
      <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
        {week.map(({ date, sessions }) => {
          const isToday = date === today
          const isSelected = date === selectedDate
          const styles = sessions.map((session) => sessionTypeStyle(session.workoutType))
          const weekday = parseLocalDate(date).toLocaleDateString(localeTag(language), { weekday: 'short' })
          // The chip reads "Tu" over a coloured dot over "Rest", which is thin
          // to listen to.  Spell out what the eye gets from the layout — and on
          // a two-a-day name both sessions, since the visible text cannot.
          const label = `${parseLocalDate(date).toLocaleDateString(localeTag(language), {
            weekday: 'long',
          })} — ${
            sessions.length
              ? sessions
                  .map((session, i) => [t(styles[i].label), session.title, formatPlanDuration(session)].filter(Boolean).join(', '))
                  .join('; ')
              : t('nothing planned')
          }`
          // A two-a-day shows the day's total, window ends summed: two durations
          // do not fit. A session without one leaves no total to claim.
          const ranges = sessions.map((session) => planDurationRange(session))
          const duration = ranges.every((range) => range)
            ? formatPlanDuration({
                durationMinMinutes: ranges.reduce((sum, range) => sum + range![0], 0),
                durationMaxMinutes: ranges.reduce((sum, range) => sum + range![1], 0),
              })
            : ''

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
              className={`flex min-h-[5.5rem] flex-col items-center rounded-xl border px-0.5 py-2 text-center transition-colors sm:px-1 ${chrome}`}
            >
              <span
                className={`text-[11px] font-semibold uppercase ${
                  isToday ? 'text-amber-600' : 'text-gray-400'
                }`}
              >
                {weekday.slice(0, 2)}
              </span>
              {/* One icon per session, so a two-a-day is visible at a glance
                  rather than only in the label below (#645) — and so the type
                  is not carried by colour alone (ai-trainer-ops#51.4, WCAG
                  1.4.1). An endurance day and a race used to be a green dot and
                  a red dot: to an athlete who cannot tell those apart, the same
                  dot. The icons are `aria-hidden`; the chip's own label already
                  names every session on the day. */}
              <span className="mt-1.5 flex items-center gap-0.5">
                {styles.length === 0 ? (
                  <span className="h-2 w-2 rounded-full bg-slate-200" />
                ) : (
                  styles.map(({ Icon, iconColor }, i) => (
                    <Icon key={i} size={13} className={iconColor} aria-hidden="true" />
                  ))
                )}
              </span>
              {/* The day's own title and its duration, at every width
                  (ai-trainer-ops#90). The type word was all a chip used to say,
                  and it said it twice: "Base" on a phone, "Endurance" on a
                  desktop. The icon above carries the type and the chip's label
                  names it.

                  A phone leaves about 44 px a column, so the title wraps at a
                  hyphen and stops after two lines. No `truncate`: one line
                  would cut every title after five letters. */}
              <span className="mt-1.5 line-clamp-2 w-full hyphens-auto break-words text-[10px] font-medium leading-tight text-gray-700 sm:text-[11px]">
                {sessions.length === 0
                  ? '—'
                  : sessions.length === 1
                    ? sessions[0].title || styles[0].label
                    : `${sessions.length} sessions`}
              </span>
              {duration && (
                <span className="mt-0.5 text-[10px] tabular-nums text-gray-500 sm:text-[11px]">{duration}</span>
              )}
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
