import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronLeft, ChevronRight, Flag } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { usePlan } from '../queries/plan'
import type { RaceEvent, RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'
import { sessionIcon } from '../utils/sessionIcon'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionKey, sessionLabel } from '../utils/planSessions'
import { isCyclingSession, planSport, planSportLabel } from '../utils/planSport'
import WeatherBadge from './WeatherBadge'
import RaceEventEditor from './RaceEventEditor'
import { addMonths, calendarWeeks, formatDistance, formatIsoDate, startOfMonth } from '../utils/calendarGrid'
import { sportTypeText } from '../utils/activityType'
import { localeTag, useLanguage, useT, type Language } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { useIsPhone } from '../hooks/useIsPhone'
import { cardFrame, cardTitle, iconButton, textButton } from './ui'

// A cell is white, a rest day or an empty one grey. The type was a colour per
// cell until the owner's review of 2026-10-24 ("kein Kunterbunt"); the icon and
// the title say it now.
const cellColor = (day: TrainingDay | null | undefined) =>
  !day || day.workoutType === 'rest' ? 'bg-gray-50 text-gray-500' : 'bg-white text-gray-800'


const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/**
 * How demanding each workout type is, for picking a two-a-day's headline
 * session — the one whose colour tints the calendar cell. An easy AM spin next
 * to a PM interval block should read as an interval day, not a recovery day.
 */
const typeWeight: Record<TrainingDay['workoutType'], number> = {
  rest: 0,
  recovery: 1,
  endurance: 2,
  strength: 3,
  tempo: 4,
  intervals: 5,
  race: 6,
}

/** The hardest session of a day, or `null` when nothing is planned. */
function headlineSession(sessions: TrainingDay[]): TrainingDay | null {
  if (sessions.length === 0) return null
  return sessions.reduce((hardest, session) =>
    typeWeight[session.workoutType] > typeWeight[hardest.workoutType] ? session : hardest
  )
}

interface Props {
  editableEvents?: boolean
  /** Overlay logged (actual) activities from rideMetricsHistory on each day (#369). */
  showLoggedActivities?: boolean
  onRaceEventAdded?: (event: RaceEvent) => void
  onRaceEventRemoved?: (event: RaceEvent) => void
  onRaceEventUpdated?: (event: RaceEvent) => void
}

/** Short duration label for a logged ride, e.g. "1h 5m" or "45m". */
function formatLoggedDuration(seconds: number | undefined, language: Language = 'en'): string {
  if (!seconds || seconds <= 0) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  // "1 h 5 min" in German, "1h 5m" in English (ai-trainer-ops#121).
  if (language === 'de') return h > 0 ? `${h} h ${m} min` : `${m} min`
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

/** Colour + label describing how a logged ride lines up with the plan (#369). */
function loggedRideStatus(
  ride: RideMetricPoint,
  hasPlan: boolean
): { dot: string; label: string } {
  switch (ride.planMatchStatus) {
    case 'auto_matched':
    case 'manual_matched':
      return { dot: 'bg-gray-400', label: 'matched to plan' }
    case 'ambiguous':
      // The one in the accent: it waits for the athlete's answer on the day page (#574).
      return { dot: 'bg-amber-500', label: 'ambiguous match' }
    default:
      return hasPlan
        ? { dot: 'bg-gray-300', label: 'unmatched' }
        : { dot: 'bg-gray-300', label: 'unplanned' }
  }
}

function rideKey(ride: RideMetricPoint): string {
  return ride.externalActivityId
    ? `external:${ride.externalActivityId}`
    : `strava:${ride.stravaActivityId}`
}

export default function TrainingCalendar({
  editableEvents = false,
  showLoggedActivities = false,
  onRaceEventAdded,
  onRaceEventRemoved,
  onRaceEventUpdated,
}: Props) {
  const navigate = useNavigate()
  const t = useT()
  const language = useLanguage()
  const planText = usePlanText()
  const isPhone = useIsPhone()
  const plan = usePlan()
  const { raceEvents, rideMetricsHistory, weatherForecast } = useAppStore(
    useShallow((s) => ({
      raceEvents: s.raceEvents,
      rideMetricsHistory: s.rideMetricsHistory,
      weatherForecast: s.weatherForecast,
    }))
  )

  const today = formatIsoDate(new Date())
  const [visibleMonth, setVisibleMonth] = useState(() => startOfMonth(new Date()))
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  const racesByDate = useMemo(() => {
    const grouped: Record<string, RaceEvent[]> = {}
    for (const event of raceEvents) {
      grouped[event.date] = [...(grouped[event.date] ?? []), event]
    }
    return grouped
  }, [raceEvents])

  const ridesByDate = useMemo(() => {
    const grouped: Record<string, RideMetricPoint[]> = {}
    if (!showLoggedActivities) return grouped
    for (const ride of rideMetricsHistory) {
      grouped[ride.activityDate] = [...(grouped[ride.activityDate] ?? []), ride]
    }
    return grouped
  }, [rideMetricsHistory, showLoggedActivities])

  const monthLabel = visibleMonth.toLocaleDateString(localeTag(language), {
    month: 'long',
    year: 'numeric',
  })

  // A tap on a day: the race editor where races are edited, else the day's page.
  const openDay = (date: string, hasContent: boolean) => {
    if (editableEvents) setSelectedDate(date)
    else if (hasContent) navigate(`/workout/${date}`)
  }

  const weeks = useMemo(
    () => calendarWeeks(visibleMonth, plan, racesByDate, ridesByDate),
    [plan, racesByDate, ridesByDate, visibleMonth]
  )

  return (
    <>
      <div className={`${cardFrame} overflow-hidden`}>
        <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2">
          <p className={cardTitle}>{monthLabel}</p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setVisibleMonth((month) => addMonths(month, -1))}
              className={iconButton}
              aria-label={t('Previous month')}
            >
              <ChevronLeft size={16} />
            </button>
            <button
              type="button"
              onClick={() => setVisibleMonth(startOfMonth(new Date()))}
              className={`${textButton} px-2`}
            >
              {t('Today')}
            </button>
            <button
              type="button"
              onClick={() => setVisibleMonth((month) => addMonths(month, 1))}
              className={iconButton}
              aria-label={t('Next month')}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
        {isPhone ? (
          // A phone gets a list, one row per day with the whole title: seven
          // 47 px columns cut "Lockere Grundlagenausfahrt" to "Lo…" and made a
          // month four screens of empty boxes (review of 2026-11-01,
          // ai-trainer-ops#338 A7).
          <ol aria-label={monthLabel} className="divide-y divide-gray-100">
            {weeks
              .flat()
              .filter(({ date, sessions, events, rides }) =>
                parseLocalDate(date).getMonth() === visibleMonth.getMonth() &&
                (editableEvents || sessions.length > 0 || events.length > 0 || rides.length > 0),
              )
              .map(({ date, sessions, events, rides }) => {
                const isPast = date < today
                const missed =
                  showLoggedActivities &&
                  isPast &&
                  rides.length === 0 &&
                  sessions.some((s) => !s.completed && s.workoutType !== 'rest')
                return (
                <li key={date}>
                  <button
                    type="button"
                    // The grid cell's name, so a day is found the same way on
                    // every width (review of #1280).
                    aria-label={t('Calendar day {date}', { date })}
                    onClick={() => openDay(date, sessions.length > 0 || rides.length > 0)}
                    className={`flex w-full gap-3 px-3 py-2.5 text-left text-sm hover:bg-gray-50 ${
                      date === today ? 'bg-amber-50' : ''
                    } ${date < today ? 'text-gray-500' : 'text-gray-900'}`}
                  >
                    <span className="w-20 shrink-0 font-medium">
                      {parseLocalDate(date).toLocaleDateString(localeTag(language), {
                        weekday: 'short',
                        day: 'numeric',
                        month: 'short',
                      })}
                    </span>
                    <span className="min-w-0 flex-1 space-y-1">
                      {sessions.map((session) => {
                        const Icon = sessionIcon(session)
                        return (
                          <span key={sessionKey(session)} className="flex items-start gap-1.5">
                            <Icon size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                            <span>
                              {planText(session.title)}
                              {session.workoutType !== 'rest' && (
                                <span className="text-gray-500"> · {formatPlanDuration(session, language)}</span>
                              )}
                              {session.completed && <Check size={12} className="ml-1 inline text-gray-500" aria-label={t('Done')} />}
                            </span>
                          </span>
                        )
                      })}
                      {events.map((event) => (
                        <span key={event.id} className="flex items-center gap-1.5 font-medium">
                          <Flag size={12} aria-hidden="true" />
                          {formatDistance(event.distanceKm, language)} km · {event.elevationM} m
                        </span>
                      ))}
                      {/* What was ridden, with the same status dot as the grid's,
                          and a planned day nothing was logged for. */}
                      {rides.map((ride) => {
                        const status = loggedRideStatus(ride, Boolean(headlineSession(sessions)))
                        const duration = formatLoggedDuration(ride.durationSeconds, language)
                        return (
                          <span key={rideKey(ride)} className="flex items-center gap-1.5 text-gray-500">
                            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.dot}`} aria-hidden="true" />
                            {t('Logged: {sport}{duration} ({status})', {
                              sport: t(sportTypeText(ride.sportType)),
                              duration: duration ? ` · ${duration}` : '',
                              status: t(status.label),
                            })}
                          </span>
                        )
                      })}
                      {missed && <span className="block text-gray-500">{t('missed')}</span>}
                      {editableEvents && sessions.length === 0 && events.length === 0 && (
                        <span className="text-gray-400">{t('Add race')}</span>
                      )}
                    </span>
                  </button>
                </li>
                )
              })}
          </ol>
        ) : (
          <>
          <div className="grid grid-cols-7 border-b border-gray-100">
            {DAYS.map((d) => (
              <div key={d} className="py-2 text-center text-xs font-medium text-gray-500 border-r border-gray-100 last:border-r-0">
                {t(d)}
              </div>
            ))}
          </div>
          {weeks.map((week, wi) => (
            <div key={wi} className="grid grid-cols-7 border-b border-gray-100 last:border-b-0">
              {week.map(({ date, sessions, events, rides }) => {
                const isToday = date === today
                const isPast = date < today
                const isVisibleMonth = parseLocalDate(date).getMonth() === visibleMonth.getMonth()
                // The cell is tinted by the day's headline session — the hardest
                // one — so a two-a-day reads as its heaviest load at a glance.
                const day = headlineSession(sessions)
                const allCompleted = sessions.length > 0 && sessions.every((s) => s.completed)
                const baseColor = cellColor(day)
                // A planned session in the past with nothing logged and no completion mark.
                const isMissed =
                  showLoggedActivities &&
                  isPast &&
                  rides.length === 0 &&
                  sessions.some((s) => !s.completed && s.workoutType !== 'rest')
                return (
                  <button
                    key={date}
                    type="button"
                    aria-label={t('Calendar day {date}', { date })}
                    onClick={() => openDay(date, sessions.length > 0 || rides.length > 0)}
                    className={`${showLoggedActivities ? 'min-h-[132px]' : 'min-h-[104px]'} border-r border-gray-100 last:border-r-0 p-1.5 text-left hover:brightness-95 transition-all relative ${baseColor} ${
                      isToday ? 'ring-2 ring-inset ring-amber-500' : ''
                    } ${!isVisibleMonth ? 'opacity-60' : ''} ${day && isPast && !allCompleted ? 'opacity-60' : ''}`}
                  >
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <span className="text-xs font-semibold">{parseLocalDate(date).getDate()}</span>
                      {allCompleted ? (
                        <Check size={12} className="text-gray-500 shrink-0" aria-hidden="true" />
                      ) : (
                        // Forecast on planned days inside the ~16-day horizon (#495).
                        // Past and unplanned days show nothing rather than a stale
                        // or irrelevant reading.
                        day && !isPast && (
                          <WeatherBadge
                            forecast={weatherForecast[date]}
                            size={10}
                            className="shrink-0 text-xs"
                          />
                        )
                      )}
                    </div>
                    {sessions.length > 0 ? (
                      <>
                        {/* Each session gets its own row, so "AM Yoga · PM
                            Endurance" is visible without opening the day (#496). */}
                        {sessions.map((session) => {
                          const label = sessionLabel(session, sessions.length)
                          return (
                            <div key={sessionKey(session)} className="mb-0.5 last:mb-0">
                              <div className="flex items-center gap-1">
                                {(() => {
                                  // `aria-hidden`: the cell's label already names
                                  // the day, and the session's own title sits
                                  // beside this. An icon that announced itself
                                  // would say "bicycle" into the middle of it.
                                  const Icon = sessionIcon(session)
                                  return <Icon size={14} className="shrink-0" aria-hidden="true" />
                                })()}
                                {label && <span className="text-xs text-gray-500">{label}</span>}
                                {/* Named, not just drawn: an emoji is ambiguous at
                                    this size, and "is Tuesday a run?" must have an
                                    unambiguous answer (#710). Cycling stays silent —
                                    it is what the whole calendar is unless stated. */}
                                {!isCyclingSession(session) && (
                                  // From sm up on screen: a phone column holds the icon and no
                                  // word beside it. A screen reader keeps the word (#710).
                                  <span className="sr-only text-xs text-gray-500 sm:not-sr-only">{t(planSportLabel(planSport(session)))}</span>
                                )}
                                {sessions.length > 1 && session.completed && (
                                  <Check size={10} className="text-gray-500 shrink-0" aria-hidden="true" />
                                )}
                              </div>
                              <p className="text-xs font-medium leading-tight truncate">
                                {planText(session.title)}
                              </p>
                              {session.workoutType !== 'rest' && (
                                <p className="text-xs text-gray-500">{formatPlanDuration(session, language)}</p>
                              )}
                            </div>
                          )
                        })}
                        {isMissed && (
                          <p className="text-xs text-gray-500">{t('missed')}</p>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-gray-400 mt-5">{editableEvents ? t('Add race') : ''}</p>
                    )}
                    {rides.length > 0 && (
                      <div className="mt-1 space-y-0.5">
                        {rides.slice(0, 2).map((ride) => {
                          const status = loggedRideStatus(ride, Boolean(day))
                          const duration = formatLoggedDuration(ride.durationSeconds, language)
                          const sport = t(sportTypeText(ride.sportType))
                          return (
                            <div
                              key={rideKey(ride)}
                              title={t('Logged: {sport}{duration} ({status})', {
                                sport,
                                duration: duration ? ` · ${duration}` : '',
                                status: t(status.label),
                              })}
                              className="flex items-center gap-1 text-xs text-gray-700"
                            >
                              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.dot}`} />
                              <span className="truncate">{duration || sport}</span>
                            </div>
                          )
                        })}
                        {rides.length > 2 && (
                          <p className="text-xs text-gray-500">{t('+{count} more', { count: rides.length - 2 })}</p>
                        )}
                      </div>
                    )}
                    {events.length > 0 && (
                      <div className="absolute left-1.5 right-1.5 bottom-1.5 space-y-1">
                        {events.slice(0, 2).map((event) => (
                          <div
                            key={event.id}
                            className="flex items-center gap-1 rounded-md border border-gray-300 bg-white px-1.5 py-0.5 text-xs font-medium text-gray-900"
                          >
                            <Flag size={10} aria-hidden="true" />
                            <span className="truncate">
                              {formatDistance(event.distanceKm, language)} km · {event.elevationM} m
                            </span>
                          </div>
                        ))}
                        {events.length > 2 && (
                          <p className="text-xs text-gray-500">{t('+{count} more', { count: events.length - 2 })}</p>
                        )}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
          </>
        )}
      </div>

      {editableEvents && selectedDate && (
        <RaceEventEditor
          key={selectedDate}
          date={selectedDate}
          onClose={() => setSelectedDate(null)}
          onSaved={(date) => {
            setSelectedDate(date)
            setVisibleMonth(startOfMonth(parseLocalDate(date)))
          }}
          onRaceEventAdded={onRaceEventAdded}
          onRaceEventRemoved={onRaceEventRemoved}
          onRaceEventUpdated={onRaceEventUpdated}
        />
      )}
    </>
  )
}
