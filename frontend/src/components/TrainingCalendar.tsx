import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronLeft, ChevronRight, Flag, Pencil, Trash2, X } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent, RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { createRaceEvent, deleteRaceEventRemote, updateRaceEventRemote } from '../services/user'
import { parseLocalDate } from '../utils/workout'
import { sessionIcon } from '../utils/sessionIcon'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionKey, sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import { isCyclingSession, planSport, planSportLabel } from '../utils/planSport'
import WeatherBadge from './WeatherBadge'
import { sportTypeText } from '../utils/activityType'
import { localeTag, useLanguage, useT, type Language } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { button, cardFrame, cardTitle, formLabel, iconButton, iconDeleteButton, input, note, textButton, unit } from './ui'

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

interface RaceEventForm {
  date: string
  startTime: string
  distanceKm: string
  elevationM: string
}

function formatIsoDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1, 12)
}

function addMonths(date: Date, amount: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1, 12)
}

function emptyForm(date: string): RaceEventForm {
  return {
    date,
    startTime: '',
    distanceKm: '',
    elevationM: '',
  }
}

function formFromEvent(event: RaceEvent): RaceEventForm {
  return {
    date: event.date,
    startTime: event.startTime ?? '',
    distanceKm: String(event.distanceKm),
    elevationM: String(event.elevationM),
  }
}

function formatDistance(value: number, language: Language): string {
  // No thousands separator, as before: a race reads "1000 km", not "1,000 km".
  return value.toLocaleString(localeTag(language), { maximumFractionDigits: 1, useGrouping: false })
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
  const {
    authToken,
    plan,
    raceEvents,
    rideMetricsHistory,
    weatherForecast,
    addRaceEvent,
    removeRaceEvent,
    updateRaceEvent,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      plan: s.trainingPlan,
      raceEvents: s.raceEvents,
      rideMetricsHistory: s.rideMetricsHistory,
      weatherForecast: s.weatherForecast,
      addRaceEvent: s.addRaceEvent,
      removeRaceEvent: s.removeRaceEvent,
      updateRaceEvent: s.updateRaceEvent,
    }))
  )

  const today = formatIsoDate(new Date())
  const [visibleMonth, setVisibleMonth] = useState(() => startOfMonth(new Date()))
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [editingEventId, setEditingEventId] = useState<string | null>(null)
  const [form, setForm] = useState<RaceEventForm>(emptyForm(today))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const weeks = useMemo(() => {
    const firstCalendarDate = startOfMonth(visibleMonth)
    const dayOfWeek = firstCalendarDate.getDay()
    const monday = new Date(firstCalendarDate)
    monday.setDate(firstCalendarDate.getDate() - ((dayOfWeek + 6) % 7))

    type Cell = {
      date: string
      /** Every session planned for the date, AM→PM; empty when nothing is planned. */
      sessions: TrainingDay[]
      events: RaceEvent[]
      rides: RideMetricPoint[]
    }
    const result: Cell[][] = []
    for (let w = 0; w < 6; w++) {
      const week: Cell[] = []
      for (let d = 0; d < 7; d++) {
        const date = new Date(monday)
        date.setDate(monday.getDate() + w * 7 + d)
        const iso = formatIsoDate(date)
        week.push({
          date: iso,
          // A date may hold a two-a-day; the old `.find()` rendered only the
          // first session and silently hid the other (#496).
          sessions: sessionsForDate(plan, iso),
          events: racesByDate[iso] ?? [],
          rides: ridesByDate[iso] ?? [],
        })
      }
      result.push(week)
    }
    return result
  }, [plan, racesByDate, ridesByDate, visibleMonth])

  const selectedEvents = selectedDate ? racesByDate[selectedDate] ?? [] : []
  const selectedPlanSessions = sessionsForDate(plan, selectedDate)
  const canSave =
    Boolean(authToken) &&
    Boolean(form.date) &&
    Number(form.distanceKm) > 0 &&
    form.elevationM !== '' &&
    Number(form.elevationM) >= 0 &&
    !saving

  const openEventEditor = (date: string, event?: RaceEvent) => {
    setSelectedDate(date)
    setEditingEventId(event?.id ?? null)
    setForm(event ? formFromEvent(event) : emptyForm(date))
    setError(null)
  }

  const closeEventEditor = () => {
    setSelectedDate(null)
    setEditingEventId(null)
    setError(null)
  }

  const saveEvent = async () => {
    if (!authToken || !canSave) return
    setSaving(true)
    setError(null)
    try {
      const payload = {
        date: form.date,
        startTime: form.startTime || null,
        distanceKm: Number(form.distanceKm),
        elevationM: Math.round(Number(form.elevationM)),
      }
      if (editingEventId) {
        const updated = await updateRaceEventRemote(authToken, editingEventId, payload)
        updateRaceEvent(updated)
        onRaceEventUpdated?.(updated)
      } else {
        const created = await createRaceEvent(authToken, payload)
        addRaceEvent(created)
        onRaceEventAdded?.(created)
      }
      setSelectedDate(payload.date)
      setVisibleMonth(startOfMonth(parseLocalDate(payload.date)))
      setEditingEventId(null)
      setForm(emptyForm(payload.date))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save race event')
    } finally {
      setSaving(false)
    }
  }

  const deleteEvent = async (event: RaceEvent) => {
    if (!authToken || saving) return
    setSaving(true)
    setError(null)
    try {
      await deleteRaceEventRemote(authToken, event.id)
      removeRaceEvent(event.id)
      onRaceEventRemoved?.(event)
      if (editingEventId === event.id) {
        setEditingEventId(null)
        setForm(emptyForm(selectedDate ?? event.date))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove race event')
    } finally {
      setSaving(false)
    }
  }

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
                  onClick={() => {
                    if (editableEvents) {
                      openEventEditor(date)
                    } else if (sessions.length > 0 || rides.length > 0) {
                      navigate(`/workout/${date}`)
                    }
                  }}
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
      </div>

      {editableEvents && selectedDate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-gray-900/40 px-4">
          <div className="w-full max-w-lg rounded-2xl border border-gray-100 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
              <div>
                <p className={cardTitle}>{t('Race events')}</p>
                <p className="text-sm text-gray-500">{selectedDate}</p>
              </div>
              <button
                type="button"
                onClick={closeEventEditor}
                className={iconButton}
                aria-label={t('Close race event editor')}
              >
                <X size={16} />
              </button>
            </div>

            <div className="space-y-4 p-4">
              {/* One link per planned session, so the evening ride is reachable
                  from the calendar and not just the morning one (#496). */}
              {selectedPlanSessions.map((session) => {
                const label = sessionLabel(session, selectedPlanSessions.length)
                const slot = sessionSlot(session)
                return (
                  <button
                    key={sessionKey(session)}
                    type="button"
                    onClick={() =>
                      navigate(
                        slot > 0
                          ? `/workout/${session.date}?slot=${slot}`
                          : `/workout/${session.date}`
                      )
                    }
                    className="block text-sm font-medium text-amber-700 hover:text-amber-800"
                  >
                    {label
                      ? t('Open {label} planned workout', { label })
                      : t('Open planned workout')}
                  </button>
                )
              })}

              {selectedEvents.length > 0 && (
                <div className="divide-y divide-gray-100">
                  {selectedEvents.map((event) => (
                    <div key={event.id} className="py-2">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 text-sm">
                          <p className="font-medium text-gray-900">
                            {t('{distance} km · {elevation} m climbing', {
                              distance: formatDistance(event.distanceKm, language),
                              elevation: event.elevationM,
                            })}
                          </p>
                          <p className="text-gray-500">
                            {event.startTime ? t('{date} at {time}', { date: event.date, time: event.startTime }) : event.date}
                          </p>
                        </div>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => openEventEditor(event.date, event)}
                            className={iconButton}
                            aria-label={t('Edit race event')}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void deleteEvent(event)}
                            disabled={saving}
                            className={iconDeleteButton}
                            aria-label={t('Remove race event')}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <label className={formLabel}>
                  {t('Date')}
                  <input
                    type="date"
                    value={form.date}
                    onChange={(e) => setForm((prev) => ({ ...prev, date: e.target.value }))}
                    className={`mt-1.5 ${input}`}
                  />
                </label>
                <label className={formLabel}>
                  {t('Time')}
                  <input
                    type="time"
                    value={form.startTime}
                    onChange={(e) => setForm((prev) => ({ ...prev, startTime: e.target.value }))}
                    className={`mt-1.5 ${input}`}
                  />
                </label>
                <label className={formLabel}>
                  {t('Distance')}
                  <div className="relative mt-1.5">
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.distanceKm}
                      onChange={(e) => setForm((prev) => ({ ...prev, distanceKm: e.target.value }))}
                      className={`pr-10 ${input}`}
                    />
                    <span className={unit}>km</span>
                  </div>
                </label>
                <label className={formLabel}>
                  {t('Climb')}
                  <div className="relative mt-1.5">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={form.elevationM}
                      onChange={(e) => setForm((prev) => ({ ...prev, elevationM: e.target.value }))}
                      className={`pr-10 ${input}`}
                    />
                    <span className={unit}>m</span>
                  </div>
                </label>
              </div>

              {error && <p className={note(false)}>{t(error)}</p>}

              <div className="flex items-center justify-end gap-3 border-t border-gray-100 pt-3">
                <button
                  type="button"
                  onClick={() => {
                    setEditingEventId(null)
                    setForm(emptyForm(selectedDate))
                  }}
                  className={textButton}
                >
                  {t('New')}
                </button>
                <button
                  type="button"
                  onClick={() => void saveEvent()}
                  disabled={!canSave}
                  className={button}
                >
                  {editingEventId ? t('Save event') : t('Add event')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
