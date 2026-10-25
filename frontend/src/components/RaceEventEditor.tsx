import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Pencil, Trash2, X } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent } from '../store/useAppStore'
import { createRaceEvent, deleteRaceEventRemote, updateRaceEventRemote } from '../services/user'
import { sessionKey, sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import { useLanguage, useT } from '../i18n'
import { formatDistance } from '../utils/calendarGrid'
import { button, cardTitle, formLabel, iconButton, iconDeleteButton, input, note, textButton, unit } from './ui'

interface RaceEventForm {
  date: string
  startTime: string
  distanceKm: string
  elevationM: string
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

interface Props {
  /** The day the calendar opened. Keyed on it, so a new day starts a new form. */
  date: string
  onClose: () => void
  /** After a save, with the day the race is on: the calendar shows its month. */
  onSaved: (date: string) => void
  onRaceEventAdded?: (event: RaceEvent) => void
  onRaceEventRemoved?: (event: RaceEvent) => void
  onRaceEventUpdated?: (event: RaceEvent) => void
}

/** The month calendar's dialog for a day's races, and its links to the day's
 *  sessions (ai-trainer-ops#274: out of `TrainingCalendar`). */
export default function RaceEventEditor({
  date: selectedDate,
  onClose,
  onSaved,
  onRaceEventAdded,
  onRaceEventRemoved,
  onRaceEventUpdated,
}: Props) {
  const navigate = useNavigate()
  const t = useT()
  const language = useLanguage()
  const { authToken, plan, raceEvents, addRaceEvent, removeRaceEvent, updateRaceEvent } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      plan: s.trainingPlan,
      raceEvents: s.raceEvents,
      addRaceEvent: s.addRaceEvent,
      removeRaceEvent: s.removeRaceEvent,
      updateRaceEvent: s.updateRaceEvent,
    }))
  )
  const [editingEventId, setEditingEventId] = useState<string | null>(null)
  const [form, setForm] = useState<RaceEventForm>(() => emptyForm(selectedDate))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedEvents = raceEvents.filter((event) => event.date === selectedDate)
  const selectedPlanSessions = sessionsForDate(plan, selectedDate)
  const canSave =
    Boolean(authToken) &&
    Boolean(form.date) &&
    Number(form.distanceKm) > 0 &&
    form.elevationM !== '' &&
    Number(form.elevationM) >= 0 &&
    !saving

  const openEventEditor = (event: RaceEvent) => {
    setEditingEventId(event.id)
    setForm(formFromEvent(event))
    setError(null)
  }

  const closeEventEditor = onClose
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
      setEditingEventId(null)
      setForm(emptyForm(payload.date))
      onSaved(payload.date)
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
        setForm(emptyForm(selectedDate))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove race event')
    } finally {
      setSaving(false)
    }
  }

  return (
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
                        onClick={() => openEventEditor(event)}
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
  )
}
