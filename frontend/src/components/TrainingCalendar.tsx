import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Activity,
  CalendarDays,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Flag,
  Mountain,
  Pencil,
  Plus,
  Route,
  Save,
  Trash2,
  X,
} from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent, RideMetricPoint, TrainingDay } from '../store/useAppStore'
import { createRaceEvent, deleteRaceEventRemote, updateRaceEventRemote } from '../services/user'
import { parseLocalDate } from '../utils/workout'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionKey, sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import WeatherBadge from './WeatherBadge'

const typeColors: Record<TrainingDay['workoutType'], string> = {
  rest: 'bg-gray-100 text-gray-500 border-gray-200',
  endurance: 'bg-blue-50 text-blue-700 border-blue-200',
  intervals: 'bg-red-50 text-red-700 border-red-200',
  tempo: 'bg-orange-50 text-orange-700 border-orange-200',
  race: 'bg-purple-50 text-purple-700 border-purple-200',
  recovery: 'bg-green-50 text-green-700 border-green-200',
  strength: 'bg-teal-50 text-teal-700 border-teal-200',
}

const typeEmoji: Record<TrainingDay['workoutType'], string> = {
  rest: '😴',
  endurance: '🚴',
  intervals: '⚡',
  tempo: '🔥',
  race: '🏆',
  recovery: '💚',
  strength: '💪',
}

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
function formatLoggedDuration(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
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
      return { dot: 'bg-emerald-500', label: 'matched to plan' }
    case 'ambiguous':
      return { dot: 'bg-amber-500', label: 'ambiguous match' }
    default:
      return hasPlan
        ? { dot: 'bg-sky-500', label: 'unmatched' }
        : { dot: 'bg-sky-500', label: 'unplanned' }
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

function formatDistance(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export default function TrainingCalendar({
  editableEvents = false,
  showLoggedActivities = false,
  onRaceEventAdded,
  onRaceEventRemoved,
  onRaceEventUpdated,
}: Props) {
  const navigate = useNavigate()
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

  const monthLabel = visibleMonth.toLocaleDateString(undefined, {
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
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <p className="text-sm font-bold text-gray-800">{monthLabel}</p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setVisibleMonth((month) => addMonths(month, -1))}
              className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-800"
              aria-label="Previous month"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              type="button"
              onClick={() => setVisibleMonth(startOfMonth(new Date()))}
              className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 hover:text-gray-900"
            >
              <CalendarDays size={14} />
              Today
            </button>
            <button
              type="button"
              onClick={() => setVisibleMonth((month) => addMonths(month, 1))}
              className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-800"
              aria-label="Next month"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
        <div className="grid grid-cols-7 border-b">
          {DAYS.map((d) => (
            <div key={d} className="py-2 text-center text-xs font-semibold text-gray-400 border-r last:border-r-0">
              {d}
            </div>
          ))}
        </div>
        {weeks.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7 border-b last:border-b-0">
            {week.map(({ date, sessions, events, rides }) => {
              const isToday = date === today
              const isPast = date < today
              const isVisibleMonth = parseLocalDate(date).getMonth() === visibleMonth.getMonth()
              // The cell is tinted by the day's headline session — the hardest
              // one — so a two-a-day reads as its heaviest load at a glance.
              const day = headlineSession(sessions)
              const allCompleted = sessions.length > 0 && sessions.every((s) => s.completed)
              const baseColor = day ? typeColors[day.workoutType] : 'bg-gray-50 text-gray-500 border-gray-200'
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
                  aria-label={`Calendar day ${date}`}
                  onClick={() => {
                    if (editableEvents) {
                      openEventEditor(date)
                    } else if (sessions.length > 0 || rides.length > 0) {
                      navigate(`/workout/${date}`)
                    }
                  }}
                  className={`${showLoggedActivities ? 'min-h-[132px]' : 'min-h-[104px]'} border-r last:border-r-0 p-1.5 text-left hover:brightness-95 transition-all relative ${baseColor} ${
                    isToday ? 'ring-2 ring-inset ring-amber-500' : ''
                  } ${!isVisibleMonth ? 'opacity-60' : ''} ${day && isPast && !allCompleted ? 'opacity-60' : ''}`}
                >
                  <div className="flex items-center justify-between gap-1 mb-0.5">
                    <span className="text-xs font-bold">{parseLocalDate(date).getDate()}</span>
                    {allCompleted ? (
                      <CheckCircle size={12} className="text-green-500 flex-shrink-0" />
                    ) : (
                      // Forecast on planned days inside the ~16-day horizon (#495).
                      // Past and unplanned days show nothing rather than a stale
                      // or irrelevant reading.
                      day && !isPast && (
                        <WeatherBadge
                          forecast={weatherForecast[date]}
                          size={10}
                          className="flex-shrink-0 text-[10px] font-semibold"
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
                              <span className="text-base leading-none">
                                {typeEmoji[session.workoutType]}
                              </span>
                              {label && (
                                <span className="text-[9px] font-bold uppercase opacity-70">
                                  {label}
                                </span>
                              )}
                              {sessions.length > 1 && session.completed && (
                                <CheckCircle size={9} className="text-green-600 flex-shrink-0" />
                              )}
                            </div>
                            <p className="text-xs font-medium leading-tight truncate">
                              {session.title}
                            </p>
                            {session.workoutType !== 'rest' && (
                              <p className="text-xs opacity-70">{formatPlanDuration(session)}</p>
                            )}
                          </div>
                        )
                      })}
                      {isMissed && (
                        <p className="text-[10px] font-semibold text-red-600">missed</p>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-gray-400 mt-5">{editableEvents ? 'Add race' : ''}</p>
                  )}
                  {rides.length > 0 && (
                    <div className="mt-1 space-y-0.5">
                      {rides.slice(0, 2).map((ride) => {
                        const status = loggedRideStatus(ride, Boolean(day))
                        const duration = formatLoggedDuration(ride.durationSeconds)
                        const sport = ride.sportType.toLowerCase().replace(/_/g, ' ')
                        return (
                          <div
                            key={rideKey(ride)}
                            title={`Logged: ${sport}${duration ? ` · ${duration}` : ''} (${status.label})`}
                            className="flex items-center gap-1 rounded border border-gray-200 bg-white/80 px-1 py-0.5 text-[10px] font-medium text-gray-700"
                          >
                            <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${status.dot}`} />
                            <Activity size={9} className="flex-shrink-0 text-gray-500" />
                            <span className="truncate">{duration || sport}</span>
                          </div>
                        )
                      })}
                      {rides.length > 2 && (
                        <p className="text-[10px] font-semibold text-gray-500">+{rides.length - 2} more</p>
                      )}
                    </div>
                  )}
                  {events.length > 0 && (
                    <div className="absolute left-1.5 right-1.5 bottom-1.5 space-y-1">
                      {events.slice(0, 2).map((event) => (
                        <div
                          key={event.id}
                          className="flex items-center gap-1 rounded-md bg-fuchsia-600 px-1.5 py-1 text-[10px] font-semibold text-white"
                        >
                          <Flag size={10} />
                          <span className="truncate">
                            {formatDistance(event.distanceKm)} km · {event.elevationM} m
                          </span>
                        </div>
                      ))}
                      {events.length > 2 && (
                        <p className="text-[10px] font-semibold text-fuchsia-700">+{events.length - 2} more</p>
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
          <div className="w-full max-w-lg rounded-xl bg-white shadow-xl border border-gray-100">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-gray-900">Race events</p>
                <p className="text-xs text-gray-500">{selectedDate}</p>
              </div>
              <button
                type="button"
                onClick={closeEventEditor}
                className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                aria-label="Close race event editor"
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
                    className="block text-xs font-semibold text-amber-700 hover:text-amber-800"
                  >
                    {label
                      ? `Open ${label} planned workout`
                      : 'Open planned workout'}
                  </button>
                )
              })}

              {selectedEvents.length > 0 && (
                <div className="space-y-2">
                  {selectedEvents.map((event) => (
                    <div key={event.id} className="rounded-lg border border-fuchsia-100 bg-fuchsia-50 px-3 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="flex items-center gap-1 text-sm font-semibold text-fuchsia-900">
                            <Flag size={14} />
                            {formatDistance(event.distanceKm)} km · {event.elevationM} m climbing
                          </p>
                          <p className="text-xs text-fuchsia-700">
                            {event.date}{event.startTime ? ` at ${event.startTime}` : ''}
                          </p>
                        </div>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => openEventEditor(event.date, event)}
                            className="rounded-lg p-1.5 text-fuchsia-700 hover:bg-fuchsia-100"
                            aria-label="Edit race event"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void deleteEvent(event)}
                            disabled={saving}
                            className="rounded-lg p-1.5 text-red-500 hover:bg-red-50 disabled:opacity-50"
                            aria-label="Remove race event"
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
                <label className="text-xs font-semibold text-gray-600">
                  Date
                  <input
                    type="date"
                    value={form.date}
                    onChange={(e) => setForm((prev) => ({ ...prev, date: e.target.value }))}
                    className="mt-1 w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
                  />
                </label>
                <label className="text-xs font-semibold text-gray-600">
                  Time
                  <div className="relative mt-1">
                    <Clock size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
                    <input
                      type="time"
                      value={form.startTime}
                      onChange={(e) => setForm((prev) => ({ ...prev, startTime: e.target.value }))}
                      className="w-full rounded-lg border-gray-300 pl-9 text-sm focus:border-amber-500 focus:ring-amber-500"
                    />
                  </div>
                </label>
                <label className="text-xs font-semibold text-gray-600">
                  Distance
                  <div className="relative mt-1">
                    <Route size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.distanceKm}
                      onChange={(e) => setForm((prev) => ({ ...prev, distanceKm: e.target.value }))}
                      className="w-full rounded-lg border-gray-300 pl-9 pr-10 text-sm focus:border-amber-500 focus:ring-amber-500"
                    />
                    <span className="pointer-events-none absolute right-3 top-2.5 text-xs text-gray-400">km</span>
                  </div>
                </label>
                <label className="text-xs font-semibold text-gray-600">
                  Climb
                  <div className="relative mt-1">
                    <Mountain size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={form.elevationM}
                      onChange={(e) => setForm((prev) => ({ ...prev, elevationM: e.target.value }))}
                      className="w-full rounded-lg border-gray-300 pl-9 pr-10 text-sm focus:border-amber-500 focus:ring-amber-500"
                    />
                    <span className="pointer-events-none absolute right-3 top-2.5 text-xs text-gray-400">m</span>
                  </div>
                </label>
              </div>

              {error && <p className="text-xs font-medium text-red-600">{error}</p>}

              <div className="flex justify-end gap-2 border-t pt-3">
                <button
                  type="button"
                  onClick={() => {
                    setEditingEventId(null)
                    setForm(emptyForm(selectedDate))
                  }}
                  className="rounded-lg px-3 py-2 text-sm font-semibold text-gray-500 hover:bg-gray-100"
                >
                  <Plus size={15} className="inline-block align-[-2px]" /> New
                </button>
                <button
                  type="button"
                  onClick={() => void saveEvent()}
                  disabled={!canSave}
                  className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  <Save size={15} />
                  {editingEventId ? 'Save event' : 'Add event'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
