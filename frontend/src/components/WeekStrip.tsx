import { Check } from 'lucide-react'
import { addDays } from 'date-fns'
import type { TrainingDay } from '../store/useAppStore'
import { sessionsForDate } from '../utils/planSessions'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { sessionTypeStyle } from '../utils/sessionType'
import { sessionIcon } from '../utils/sessionIcon'
import { formatPlanDuration, planDurationRange } from '../utils/planDuration'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText, usePlanTextPending } from '../hooks/usePlanTranslations'
import { useProfile } from '../queries/profile'
import { weekdayKey, withoutOwnWeekday } from '../utils/planTitle'
import { cardTitle, textButton } from './ui'

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
  const planText = usePlanText()
  const isPending = usePlanTextPending()
  // `Weekday`: lowercase English names, as onboarding stores them and weekdayKey returns.
  const trainingDays = new Set((useProfile()?.trainingDays ?? []).map((day) => day.toLowerCase()))
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
        <h2 className={cardTitle}>{t('Your week')}</h2>
        {onShowMore && (
          <button type="button" onClick={onShowMore} className={textButton}>
            {/* It opens the calendar, so it says so (ai-trainer-ops#338 A7). */}
            {t('Calendar')}
          </button>
        )}
      </div>
      <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
        {week.map(({ date, sessions }) => {
          const isToday = date === today
          const isSelected = date === selectedDate
          const styles = sessions.map((session) => sessionTypeStyle(session.workoutType))
          // The calendar's rule, so a run is a runner here too: the workout
          // type alone drew a bicycle for every endurance day (ai-trainer-ops#325).
          const icons = sessions.map((session) => sessionIcon(session))
          const weekday = parseLocalDate(date).toLocaleDateString(localeTag(language), { weekday: 'short' })
          // A rest day on one of the athlete's training days is the plan holding
          // back on purpose, not a day they cannot train: next to "four training
          // days" it read as if rest counted as one (ai-trainer-ops#324).
          const keptFree =
            sessions.length > 0 &&
            sessions.every((session) => session.workoutType === 'rest') &&
            trainingDays.has(weekdayKey(date))
          // The chip reads "Tu" over an icon over "Rest", which is thin to
          // listen to.  Spell out what the eye gets from the layout — and on
          // a two-a-day name both sessions, since the visible text cannot.
          const label = `${parseLocalDate(date).toLocaleDateString(localeTag(language), {
            weekday: 'long',
          })} — ${
            sessions.length
              ? sessions
                  .map((session, i) => [t(styles[i].label), planText(session.title), formatPlanDuration(session, language)].filter(Boolean).join(', '))
                  .join('; ')
              : t('nothing planned')
          }${keptFree ? `; ${t('a training day, kept free')}` : ''}`
          // A two-a-day shows the day's total, window ends summed: two durations
          // do not fit. A session without one leaves no total to claim.
          const ranges = sessions.map((session) => planDurationRange(session))
          const duration = ranges.every((range) => range)
            ? formatPlanDuration({
                durationMinMinutes: ranges.reduce((sum, range) => sum + range![0], 0),
                durationMaxMinutes: ranges.reduce((sum, range) => sum + range![1], 0),
              }, language)
            : ''

          // The selected day is the chosen chip, in the one accent. Today keeps
          // its reference point underneath it in bold type, not a second colour.
          const chrome = isSelected
            ? 'border-amber-500 bg-amber-50'
            : 'border-gray-200 bg-white hover:border-gray-300'
          // The one title the chip shows, and the language it is in: an English
          // title in a German interface was hyphenated with German rules, found
          // no break and was cut mid-word (ai-trainer-ops#324). Untranslated
          // means English: the plan is written and stored in English, whoever
          // the athlete is, because the ride matching reads it in English
          // (backend prompts.py, ATHLETE_LANGUAGE_RULE and the plan-text
          // translation, ai-trainer-ops#121). Only a title the athlete typed
          // themselves can be another language, and then wraps as before.
          const shownTitle = sessions.length === 1 ? planText(sessions[0].title) : ''
          const titleLang = shownTitle && shownTitle === sessions[0].title ? 'en' : undefined

          return (
            <button
              key={date}
              type="button"
              onClick={() => onSelect(date)}
              aria-label={label}
              aria-pressed={isSelected}
              aria-current={isToday ? 'date' : undefined}
              className={`flex min-h-[5.5rem] flex-col items-center rounded-xl border px-0.5 py-2 text-center text-gray-500 transition-colors sm:px-1 ${chrome}${keptFree ? ' border-dashed' : ''}`}
            >
              <span className={`text-sm ${isToday ? 'font-semibold text-gray-900' : ''}`}>{weekday.slice(0, 2)}</span>
              {/* One icon per session, so a two-a-day is visible at a glance
                  rather than only in the label below (#645) — and so the type
                  is not carried by colour alone (ai-trainer-ops#51.4, WCAG
                  1.4.1). The icons are `aria-hidden`; the chip's own label
                  already names every session on the day. */}
              <span className="mt-1.5 flex items-center gap-0.5 text-gray-400">
                {icons.length === 0 ? (
                  <span className="h-2 w-2 rounded-full bg-gray-200" />
                ) : (
                  icons.map((Icon, i) => <Icon key={i} size={13} aria-hidden="true" />)
                )}
              </span>
              {/* The day's own title and its duration, at every width
                  (ai-trainer-ops#90). A phone leaves about 44 px a column, so
                  there the title is a size smaller, wraps at a hyphen and stops
                  after two lines. No `truncate`: one line would cut every title
                  after five letters. */}
              {/* Dimmed while its German is being written (ai-trainer-ops#271). */}
              <span
                lang={titleLang}
                className={`mt-1.5 line-clamp-2 w-full hyphens-auto break-words text-xs leading-tight text-gray-700 sm:text-sm${
                  sessions.length === 1 && isPending(sessions[0].title) ? ' opacity-60' : ''
                }`}
              >
                {sessions.length === 0
                  ? '—'
                  : sessions.length === 1
                    ? (shownTitle && withoutOwnWeekday(shownTitle, date)) || t(styles[0].label)
                    : t('{count} sessions', { count: sessions.length })}
              </span>
              {duration && <span className="mt-0.5 text-xs tabular-nums sm:text-sm">{duration}</span>}
              {keptFree && <span className="mt-0.5 text-[10px] leading-tight text-gray-400 sm:text-xs">{t('kept free')}</span>}
              {sessions.length > 0 && sessions.every((s) => s.completed) && (
                <Check size={12} className="mt-1 text-gray-500" aria-hidden="true" />
              )}
            </button>
          )
        })}
      </div>
    </section>
  )
}
