import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { DailyForecast, TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionKey, sessionLabel, sessionSlot } from '../utils/planSessions'
import { isCyclingSession, planSport, planSportLabel } from '../utils/planSport'
import { sessionTypeStyle } from '../utils/sessionType'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import WeatherBadge from './WeatherBadge'
import WorkoutDetails from './WorkoutDetails'
import WrongFlag from './WrongFlag'
import { button, card, dotted } from './ui'
import { localeTag, useLanguage, useT } from '../i18n'
import { sessionTexts, usePlanText, usePlanTextPending } from '../hooks/usePlanTranslations'

function SessionBlock({
  day,
  label,
  isDone,
  forecast,
}: {
  day: TrainingDay
  /** "AM" / "2/2" on a two-a-day, empty on a normal day (#645). */
  label: string
  isDone: boolean
  /** Only the first session gets it: it is the same forecast twice otherwise. */
  forecast: DailyForecast | undefined
}) {
  const t = useT()
  const language = useLanguage()
  const planText = usePlanText()
  const isPending = usePlanTextPending()
  const isRest = day.workoutType === 'rest'
  // While the German of this session is being written, its English shows
  // dimmed and the grey line says the German follows (ai-trainer-ops#271).
  const translating = sessionTexts(day).some(isPending)
  const dimmed = translating ? ' opacity-60' : ''

  // One grey line says what the pills, icons and the done badge used to say
  // (owner's review, 2026-10-24: one text style, no coloured badges). The
  // sport is named when it is not the one the whole app assumes (#710): an
  // "endurance" over a planned run reads as a ride.
  const meta = [
    label,
    t(sessionTypeStyle(day.workoutType).label),
    !isCyclingSession(day) && t(planSportLabel(planSport(day))),
    !isRest && formatPlanDuration(day, language),
    day.targetPower && `${day.targetPower.low}–${day.targetPower.high} W`,
    day.targetHeartRate && t('{low}–{high} bpm', { low: day.targetHeartRate.low, high: day.targetHeartRate.high }),
    isDone && t('Done'),
    translating && t('Translation on its way …'),
  ].filter((part): part is string => Boolean(part))

  return (
    <>
      <h2 className={`text-lg font-semibold text-gray-900${dimmed}`}>{planText(day.title)}</h2>
      <p className="mt-1 text-sm text-gray-500">
        {meta.map((part) => (
          <span key={part} className={dotted}>
            {part}
          </span>
        ))}
        <WeatherBadge forecast={forecast} className="ml-2 align-middle" />
      </p>

      {day.description && (
        <p className={`mt-3 max-w-2xl text-sm text-gray-700${dimmed}`}>{planText(day.description)}</p>
      )}

      {/* What the athlete rides, in the card they are already looking at (#634).
          The summary row is suppressed because the line above already is one. */}
      <div className={translating ? 'opacity-60' : undefined}>
        <WorkoutDetails day={day} showSummary={false} />
      </div>
      <WrongFlag
        target="day"
        flagRef={`${day.date}#${sessionSlot(day)}`}
        excerpt={[planText(day.title), formatPlanDuration(day, language), planText(day.description)].filter(Boolean).join('\n')}
      />
    </>
  )
}

/** The sessions the athlete is looking at, given the weight they actually have.
 *
 * This used to be half of one grey line ("Today: Zone 2 endurance · Tomorrow:
 * Rest") above a chat log that filled the screen — the one thing the athlete
 * opens the app for, rendered smaller than everything around it.
 *
 * Since #634 it is also where the week strip reports: picking a day re-renders
 * this card instead of navigating away, and the session is shown whole rather
 * than behind a "Show details" button.  Was `TodaySessionHero` until then, which
 * stopped being true the moment it could show Friday.
 *
 * It takes a *list* since #645. A date can hold a two-a-day, and taking one
 * `TrainingDay` meant the caller had to pick — which it did with
 * `plan.find((d) => d.date === selected)`, the exact pattern #496 removed.
 *
 * A white card like every other since the owner's review of 2026-10-24; it was
 * the one dark card on the page. The session's title is what makes it first.
 */
export default function SessionHero({
  sessions,
  date,
  doneKeys,
  onLog,
}: {
  sessions: TrainingDay[]
  /** The selected day, needed even when the plan has nothing for it — an empty
   *  Thursday still has to say which day it is empty for. */
  date: string
  /** Session keys already ridden.  Per session rather than per date: one ride
   *  on a two-a-day must not mark both done (see utils/sessionCompletion). */
  doneKeys: Set<string>
  /** Log a session from here rather than three clicks deep (ai-trainer-ops#52).
   *  Offered only where logging makes sense: not done yet, not a rest day, and
   *  not in the future. */
  onLog?: (session: TrainingDay) => void
}) {
  const t = useT()
  const language = useLanguage()
  const forecast = useAppStore(useShallow((s) => s.weatherForecast[date]))
  const isToday = date === formatLocalDate(new Date())
  // "Today" while it is, the weekday otherwise.  A bare date would make the
  // athlete do the conversion the strip they just clicked already did for them.
  const when = isToday
    ? t('Today')
    : parseLocalDate(date).toLocaleDateString(localeTag(language), { weekday: 'long' })

  if (sessions.length === 0) {
    return (
      <section className={`${card} text-center`} data-testid="session-hero">
        <p className="text-sm font-semibold text-gray-900">
          {isToday
            ? t('No session planned for today')
            : t('No session planned for {day}', { day: when })}
        </p>
        <p className="mt-1 text-sm text-gray-500">{t('Ask your coach below to put one in.')}</p>
      </section>
    )
  }

  return (
    <section className={card} data-testid="session-hero">
      {/* The day is named once, however many sessions it holds. */}
      <p className="text-sm font-medium text-gray-500">{when}</p>

      {sessions.map((session, index) => (
        <div key={sessionKey(session)} className={index > 0 ? 'mt-6 border-t border-gray-100 pt-5' : 'mt-1'}>
          <SessionBlock
            day={session}
            label={sessionLabel(session, sessions.length)}
            isDone={doneKeys.has(sessionKey(session))}
            forecast={index === 0 ? forecast : undefined}
          />
          {onLog &&
            !doneKeys.has(sessionKey(session)) &&
            session.workoutType !== 'rest' &&
            date <= formatLocalDate(new Date()) && (
              <button type="button" onClick={() => onLog(session)} className={`${button} mt-4`}>
                {sessions.length > 1
                  ? t('Log {session}', { session: sessionLabel(session, sessions.length) })
                  : t('Log it')}
              </button>
            )}
        </div>
      ))}
    </section>
  )
}
