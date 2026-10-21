import { useState } from 'react'
import { Bike, Dumbbell, Footprints } from 'lucide-react'
import Modal from './Modal'
import FitFileUpload from './FitFileUpload'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay, WorkoutFeedback } from '../store/useAppStore'
import {
  addManualActivity,
  updateManualActivity,
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  type ManualActivityInput,
} from '../services/user'
import { EFFORT_LEVELS, effortLabelsFor, type EffortLevel } from '../utils/effort'
import { formatLocalDate } from '../utils/workout'
import { sessionKey, sessionsForDate } from '../utils/planSessions'
import { loggedSessionKeys } from '../utils/sessionCompletion'
import { planSport } from '../utils/planSport'
import type { ReportedSession } from '../utils/reportedSession'
import { useT } from '../i18n'
import { planDurationRange } from '../utils/planDuration'

/** The minutes a planned session names: its window when it has one, as every
 *  other card shows it, not the midpoint stored beside it (ai-trainer-ops#180). */
function plannedMinutes(day: TrainingDay): string | number | undefined {
  const range = planDurationRange(day)
  if (!range) return day.durationMinutes
  return range[0] === range[1] ? range[0] : `${range[0]}–${range[1]}`
}

/**
 * One form for "I did a session" (ai-trainer-ops#171).
 *
 * There used to be two: "Log it" on a planned session's card, and "Add
 * activity" for anything outside the plan (#47). Which one to press depended on
 * the plan slot, which the athlete cannot see, and the wrong one left the
 * planned session open, so the nightly run carried it forward (#170). Now the
 * form asks: when the day holds an open planned session, "was it this one?"
 * decides whether the log ticks that session or becomes a session of its own.
 * Opened from a card, that session is the answer already.
 *
 * Optional per sport (#169): a ride takes the head unit's normalized power and
 * the average heart rate, a run the heart rate. A strength session takes
 * neither, and no distance.
 *
 * From the chat (#166) it opens prefilled with what the athlete wrote; they
 * fill in the rest and confirm, and the plan question is asked as always.
 */

type Sport = ManualActivityInput['sport']

const SPORTS: { value: Sport; label: string; Icon: typeof Bike }[] = [
  { value: 'cycling', label: 'Ride', Icon: Bike },
  { value: 'running', label: 'Run', Icon: Footprints },
  { value: 'strength', label: 'Strength', Icon: Dumbbell },
]

// The server's bounds (schemas.ManualActivityRequest, WorkoutFeedbackRequest):
// a value outside them is a 422, so the form refuses it before it is sent.
const MAX_MINUTES = 1440
const MIN_HEART_RATE = 30
const MAX_HEART_RATE = 250
const MAX_NORMALIZED_POWER = 2500
const MAX_DISTANCE_KM = 1000
const MAX_NOTES = 2000

/** An entered activity being corrected rather than a new one being entered. */
export interface EditedActivity {
  date: string
  slot: number
  sport: Sport
  durationMinutes: number
  perceivedEffort: EffortLevel
  /** Empty string when the athlete never said, which is not "0 km". */
  distanceKm: string
  /** Empty when not said, as the distance (ai-trainer-ops#169). */
  averageHeartRate?: string
  normalizedPower?: string
  notes: string
}

function asSport(day: TrainingDay): Sport {
  const sport = planSport(day)
  return sport === 'running' || sport === 'strength' ? sport : 'cycling'
}

/** Blank is "not measured" (`null`); anything else must be a distance the
 * backend takes, more than 0 and at most 1000 km, or the form says no
 * (`undefined`). */
function optionalDistance(text: string): number | null | undefined {
  if (!text.trim()) return null
  const value = Number(text)
  return Number.isFinite(value) && value > 0 && value <= MAX_DISTANCE_KM ? value : undefined
}

/** A whole number in [min, max], `null` for an empty field, `undefined` when invalid. */
function optionalWhole(text: string, min: number, max: number): number | null | undefined {
  if (!text.trim()) return null
  const value = Number(text)
  return Number.isInteger(value) && value >= min && value <= max ? value : undefined
}

export default function LogSessionDialog({
  open,
  onClose,
  editing = null,
  session = null,
  prefill = null,
  onLogPlanned,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  /** Set to correct an existing entry (#47): same form, prefilled, day fixed. */
  editing?: EditedActivity | null
  /** Set when opened from a planned session's card: that session is the answer. */
  session?: TrainingDay | null
  /** What the athlete reported in the chat (#166): the starting values. */
  prefill?: ReportedSession | null
  /** Logs a planned session (useLogPlannedSession). Without it the form offers
   *  no plan session and every entry is a session of its own. */
  onLogPlanned?: (day: TrainingDay, feedback: WorkoutFeedback) => Promise<void>
  /** Called once a save went through, before the form closes. */
  onSaved?: () => void
}) {
  const authToken = useAppStore((s) => s.authToken)
  const trainingPlan = useAppStore((s) => s.trainingPlan)
  const rides = useAppStore((s) => s.rideMetricsHistory)
  const setRideMetricsHistory = useAppStore((s) => s.setRideMetricsHistory)
  const setMetricsHistory = useAppStore((s) => s.setMetricsHistory)
  const t = useT()

  const today = formatLocalDate(new Date())
  // One rule: while correcting, every field starts at what was entered; from a
  // card, at the session; otherwise at the defaults.
  const said = (value: number | undefined) => (value != null ? String(value) : '')
  const [date, setDate] = useState(
    editing ? editing.date : session ? session.date : (prefill?.date ?? today)
  )
  const [sport, setSport] = useState<Sport>(
    editing ? editing.sport : session ? asSport(session) : (prefill?.sport ?? 'cycling')
  )
  const [minutes, setMinutes] = useState(
    editing
      ? String(editing.durationMinutes)
      : session
        ? String(session.durationMinutes)
        : said(prefill?.durationMinutes)
  )
  const [effort, setEffort] = useState<EffortLevel>(editing ? editing.perceivedEffort : 3)
  const [distance, setDistance] = useState(editing ? editing.distanceKm : said(prefill?.distanceKm))
  const [heartRate, setHeartRate] = useState(
    editing ? (editing.averageHeartRate ?? '') : said(prefill?.averageHeartRate)
  )
  const [power, setPower] = useState(
    editing ? (editing.normalizedPower ?? '') : said(prefill?.normalizedPower)
  )
  const [notes, setNotes] = useState(editing ? editing.notes : '')
  // `null` until the athlete answers the plan question: the answer then
  // follows the day and the sport (see `planned` below).
  const [planChoice, setPlanChoice] = useState<string | null>(null)
  // Enter it by hand, or bring the file the device recorded (ai-trainer-ops#48):
  // one entry point for "something happened that the app does not know about".
  const [mode, setMode] = useState<'enter' | 'upload'>('enter')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // The planned sessions this could be: open, not rest, on the chosen day.
  const daySessions = sessionsForDate(trainingPlan, date)
  const done = loggedSessionKeys(daySessions, rides, date)
  const candidates =
    editing || !onLogPlanned
      ? []
      : session
        ? [session]
        : daySessions.filter((day) => day.workoutType !== 'rest' && !done.has(sessionKey(day)))
  const sameSport = candidates.filter((day) => asSport(day) === sport)
  const automatic = session ? sessionKey(session) : sameSport.length === 1 ? sessionKey(sameSport[0]) : ''
  const plannedKey = planChoice ?? automatic
  const planned = candidates.find((day) => sessionKey(day) === plannedKey) ?? null

  const labels = effortLabelsFor({ workoutType: sport === 'strength' ? 'strength' : 'endurance' })
  const duration = Number(minutes)
  const hasPower = sport === 'cycling'
  const hasHeartRate = sport !== 'strength'
  const parsedHeartRate = hasHeartRate ? optionalWhole(heartRate, MIN_HEART_RATE, MAX_HEART_RATE) : null
  const parsedPower = hasPower ? optionalWhole(power, 1, MAX_NORMALIZED_POWER) : null
  const parsedDistance = sport === 'strength' ? null : optionalDistance(distance)
  // A card can open a day that has not come yet; its day is fixed, so say why
  // the form cannot be saved instead of only greying the button out.
  const future = Boolean(date) && date > today
  const canSave =
    Boolean(date) &&
    !future &&
    Number.isInteger(duration) &&
    duration > 0 &&
    duration <= MAX_MINUTES &&
    parsedHeartRate !== undefined &&
    parsedPower !== undefined &&
    parsedDistance !== undefined

  const close = () => {
    setError('')
    setMode('enter')
    onClose()
  }

  const choosePlan = (key: string) => {
    setPlanChoice(key)
    const day = candidates.find((d) => sessionKey(d) === key)
    if (day) setSport(asSport(day))
  }

  const refreshLoad = async (token: string) => {
    // The entry is a session in the load chain now; show it, and the fatigue
    // it added, without waiting for the next full reload.
    const [rideList, metrics] = await Promise.all([
      fetchRideMetricsHistory(token),
      fetchMetricsHistory(token),
    ])
    setRideMetricsHistory(rideList)
    setMetricsHistory(metrics)
  }

  const save = async () => {
    if (!authToken || !canSave) return
    setSaving(true)
    setError('')
    const distanceKm = parsedDistance ?? null
    try {
      if (planned && onLogPlanned) {
        // The planned session's own log: it ticks the session, and the coach
        // rates it (useLogPlannedSession refreshes the lists itself).
        await onLogPlanned(planned, {
          actualDurationMinutes: duration,
          perceivedEffort: effort,
          notes: notes.trim(),
          // Logged later than the day: the log belongs to that day, as an
          // entered session's does (noon, like routers/users.py).
          completedAt: date === today ? new Date().toISOString() : `${date}T12:00:00`,
          ...(parsedHeartRate != null ? { averageHeartRate: parsedHeartRate } : {}),
          ...(parsedPower != null ? { normalizedPower: parsedPower } : {}),
          ...(sport !== 'strength' && distanceKm != null ? { distanceKm } : {}),
        })
        onSaved?.()
        close()
        return
      }
      // `null` clears a stored value. Strength sends no distance or heart-rate
      // key, so the backend keeps them on a sport correction. Normalized power
      // is always sent: watts mean something only on a ride, so a ride
      // corrected into a run loses its NP rather than keeping one.
      const fields = {
        sport,
        durationMinutes: duration,
        perceivedEffort: effort,
        notes: notes.trim(),
        ...(sport === 'strength' ? {} : { distanceKm, averageHeartRate: parsedHeartRate }),
        normalizedPower: parsedPower,
      }
      if (editing) {
        await updateManualActivity(authToken, editing.date, editing.slot, fields)
      } else {
        await addManualActivity(authToken, { date, ...fields })
      }
      await refreshLoad(authToken)
      onSaved?.()
      close()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the activity.')
    } finally {
      setSaving(false)
    }
  }

  const title = editing ? t('Edit activity') : t('Log a session')
  const fixedDay = Boolean(editing || session)

  return (
    <Modal open={open} onClose={close} title={title}>
      {!fixedDay && (
        <div className="mb-4 grid grid-cols-2 gap-1 rounded-xl bg-gray-100 p-1" role="tablist">
          {(
            [
              ['enter', 'Enter it'],
              ['upload', 'Upload a file'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              onClick={() => setMode(value)}
              className={`rounded-lg py-1.5 text-sm font-semibold transition-colors ${
                mode === value ? 'bg-white text-gray-900 shadow-xs' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t(label)}
            </button>
          ))}
        </div>
      )}
      {mode === 'upload' && !fixedDay ? (
        <FitFileUpload embedded />
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <div>
            <label htmlFor="activity-date" className="block text-sm font-medium text-gray-700 mb-1">
              {t('Day')}
            </label>
            <input
              id="activity-date"
              type="date"
              value={date}
              max={today}
              disabled={fixedDay}
              onChange={(e) => {
                setDate(e.target.value)
                setPlanChoice(null)
              }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          {/* The question that used to be which button to press (#170). */}
          {candidates.length > 0 && !session && (
            <fieldset>
              <legend className="block text-sm font-medium text-gray-700 mb-1">
                {t('Was this a planned session?')}
              </legend>
              <div className="space-y-1.5">
                {[...candidates.map((day) => [sessionKey(day), day] as const), ['', null] as const].map(
                  ([key, day]) => (
                    <label
                      key={key || 'none'}
                      className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                        plannedKey === key ? 'border-amber-500 bg-amber-50' : 'border-gray-300'
                      }`}
                    >
                      <input
                        type="radio"
                        name="planned-session"
                        checked={plannedKey === key}
                        onChange={() => choosePlan(key)}
                      />
                      {day
                        ? t('Yes: {title} ({minutes} min)', { title: day.title, minutes: plannedMinutes(day) })
                        : t('No, an extra session')}
                    </label>
                  )
                )}
              </div>
            </fieldset>
          )}

          <div>
            <span id="activity-sport" className="block text-sm font-medium text-gray-700 mb-1">
              {t('Sport')}
            </span>
            {/* A planned session's sport is the plan's: changing it here would
                log a run against a planned ride. */}
            <div className="grid grid-cols-3 gap-2" role="group" aria-labelledby="activity-sport">
              {SPORTS.map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={sport === value}
                  disabled={Boolean(session) && sport !== value}
                  onClick={() => {
                    setSport(value)
                    // Drop a picked session of another sport; an explicit "No"
                    // still fits, so it stays.
                    const picked = candidates.find((d) => sessionKey(d) === planChoice)
                    if (picked && asSport(picked) !== value) setPlanChoice(null)
                  }}
                  className={`flex items-center justify-center gap-1.5 rounded-lg border py-2.5 text-sm font-semibold transition-colors disabled:opacity-40 ${
                    sport === value
                      ? 'bg-amber-500 border-amber-500 text-white'
                      : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                  }`}
                >
                  <Icon size={15} aria-hidden="true" />
                  {t(label)}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="activity-minutes" className="block text-sm font-medium text-gray-700 mb-1">
              {t('Duration (minutes)')}
            </label>
            <input
              id="activity-minutes"
              type="number"
              inputMode="numeric"
              min={1}
              max={MAX_MINUTES}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              placeholder={t('e.g. {example}', { example: 45 })}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          {/* Only for the sports that cover ground. A gym session has no distance,
              and offering the field would invite a number that means nothing — the
              strength model reads sets, reps and load (#714). Optional even for
              those: leaving it blank is "I did not measure", and the backend
              stores that as absent rather than as 0 km. */}
          {sport !== 'strength' && (
            <div>
              <label htmlFor="activity-distance" className="block text-sm font-medium text-gray-700 mb-1">
                {t('Distance (km)')} <span className="text-gray-400">— optional</span>
              </label>
              <input
                id="activity-distance"
                type="number"
                inputMode="decimal"
                min={0}
                max={MAX_DISTANCE_KM}
                step="0.1"
                value={distance}
                onChange={(e) => setDistance(e.target.value)}
                placeholder={t('e.g. {example}', { example: 10.5 })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
          )}

          {hasHeartRate && (
            <div className={`grid gap-3 ${hasPower ? 'grid-cols-2' : 'grid-cols-1'}`}>
              {hasPower && (
                <div>
                  <label htmlFor="activity-power" className="block text-sm font-medium text-gray-700 mb-1">
                    {t('Normalized power (W)')} <span className="text-gray-400">— optional</span>
                  </label>
                  <input
                    id="activity-power"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={MAX_NORMALIZED_POWER}
                    value={power}
                    onChange={(e) => setPower(e.target.value)}
                    placeholder={t('e.g. {example}', { example: 200 })}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
              )}
              <div>
                <label htmlFor="activity-heart-rate" className="block text-sm font-medium text-gray-700 mb-1">
                  {t('Avg heart rate (bpm)')} <span className="text-gray-400">— optional</span>
                </label>
                <input
                  id="activity-heart-rate"
                  type="number"
                  inputMode="numeric"
                  min={MIN_HEART_RATE}
                  max={MAX_HEART_RATE}
                  value={heartRate}
                  onChange={(e) => setHeartRate(e.target.value)}
                  placeholder={t('e.g. {example}', { example: 140 })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                />
              </div>
            </div>
          )}

          <div>
            <div className="flex items-baseline justify-between gap-2 mb-1">
              <span id="activity-effort" className="block text-sm font-medium text-gray-700">
                {t('How hard was it?')}
              </span>
              <span className="text-sm font-semibold text-amber-600" aria-hidden="true">
                {t(labels[effort])}
              </span>
            </div>
            <div className="grid grid-cols-5 gap-2" role="group" aria-labelledby="activity-effort">
              {EFFORT_LEVELS.map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={effort === n}
                  aria-label={t('{n} of 5 — {label}', { n, label: t(labels[n]) })}
                  onClick={() => setEffort(n)}
                  className={`rounded-lg border py-2.5 text-sm font-semibold transition-colors ${
                    effort === n
                      ? 'bg-amber-500 border-amber-500 text-white'
                      : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
            {/* The ends of the scale, so 1 and 5 are readable without being
                selected first. */}
            <div className="mt-1 flex justify-between px-0.5 text-[11px] text-gray-500" aria-hidden="true">
              <span>{t(labels[1])}</span>
              <span>{t(labels[5])}</span>
            </div>
          </div>

          <div>
            <label htmlFor="activity-notes" className="block text-sm font-medium text-gray-700 mb-1">
              {t('Notes')} <span className="font-normal text-gray-400">optional</span>
            </label>
            <textarea
              id="activity-notes"
              rows={2}
              maxLength={MAX_NOTES}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={
                sport === 'strength'
                  ? t('Exercises, sets, load, soreness, or anything the coach should know')
                  : t('How did it feel?')
              }
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          {future && <p className="text-sm text-gray-500">{t('This day has not come yet.')}</p>}
          {error && (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <p className="font-medium">{t('The session was not saved.')}</p>
              <p>{t(error)}</p>
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={close}
              className="flex-1 rounded-xl border border-gray-300 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              {t('Cancel')}
            </button>
            <button
              type="submit"
              disabled={!canSave || saving}
              className="flex-1 rounded-xl bg-amber-500 py-2.5 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
            >
              {saving ? t('Saving…') : editing ? t('Save changes') : error ? t('Try again') : t('Save session')}
            </button>
          </div>
        </form>
      )}
    </Modal>
  )
}
