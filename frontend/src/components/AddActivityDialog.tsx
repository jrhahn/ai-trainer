import { useState } from 'react'
import { Bike, Dumbbell, Footprints } from 'lucide-react'
import Modal from './Modal'
import { useAppStore } from '../store/useAppStore'
import {
  addManualActivity,
  fetchMetricsHistory,
  fetchRideMetricsHistory,
  type ManualActivityInput,
} from '../services/user'
import { EFFORT_LEVELS, effortLabelsFor, type EffortLevel } from '../utils/effort'
import { formatLocalDate } from '../utils/workout'

/**
 * Enter a session nothing recorded and no plan day holds (ai-trainer-ops#47).
 *
 * A ride on a rest day, a run when a ride was planned, a second session, a
 * day the plan never covered. Five fields, because the athlete with no device
 * is exactly who this is for: day, sport, minutes, how hard, and a note.
 * Power and heart rate belong to recordings and to the planned-session log.
 */

const SPORTS: { value: ManualActivityInput['sport']; label: string; Icon: typeof Bike }[] = [
  { value: 'cycling', label: 'Ride', Icon: Bike },
  { value: 'running', label: 'Run', Icon: Footprints },
  { value: 'strength', label: 'Strength', Icon: Dumbbell },
]

export default function AddActivityDialog({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const authToken = useAppStore((s) => s.authToken)
  const setRideMetricsHistory = useAppStore((s) => s.setRideMetricsHistory)
  const setMetricsHistory = useAppStore((s) => s.setMetricsHistory)

  const today = formatLocalDate(new Date())
  const [date, setDate] = useState(today)
  const [sport, setSport] = useState<ManualActivityInput['sport']>('cycling')
  const [minutes, setMinutes] = useState('')
  const [effort, setEffort] = useState<EffortLevel>(3)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const labels = effortLabelsFor({ workoutType: sport === 'strength' ? 'strength' : 'endurance' })
  const duration = Number(minutes)
  const canSave = Boolean(date) && date <= today && Number.isInteger(duration) && duration > 0

  const reset = () => {
    setDate(today)
    setSport('cycling')
    setMinutes('')
    setEffort(3)
    setNotes('')
    setError('')
  }

  const close = () => {
    reset()
    onClose()
  }

  const save = async () => {
    if (!authToken || !canSave) return
    setSaving(true)
    setError('')
    try {
      await addManualActivity(authToken, {
        date,
        sport,
        durationMinutes: duration,
        perceivedEffort: effort,
        notes: notes.trim(),
      })
      // The entry is a session in the load chain now; show it, and the
      // fatigue it added, without waiting for the next full reload.
      const [rides, metrics] = await Promise.all([
        fetchRideMetricsHistory(authToken),
        fetchMetricsHistory(authToken),
      ])
      setRideMetricsHistory(rides)
      setMetricsHistory(metrics)
      close()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the activity.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={close} title="Add activity">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <div>
          <label htmlFor="activity-date" className="block text-sm font-medium text-gray-700 mb-1">
            Day
          </label>
          <input
            id="activity-date"
            type="date"
            value={date}
            max={today}
            onChange={(e) => setDate(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
        </div>

        <div>
          <span id="activity-sport" className="block text-sm font-medium text-gray-700 mb-1">
            Sport
          </span>
          <div className="grid grid-cols-3 gap-2" role="group" aria-labelledby="activity-sport">
            {SPORTS.map(({ value, label, Icon }) => (
              <button
                key={value}
                type="button"
                aria-pressed={sport === value}
                onClick={() => setSport(value)}
                className={`flex items-center justify-center gap-1.5 rounded-lg border py-2.5 text-sm font-semibold transition-colors ${
                  sport === value
                    ? 'bg-amber-500 border-amber-500 text-white'
                    : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                }`}
              >
                <Icon size={15} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="activity-minutes" className="block text-sm font-medium text-gray-700 mb-1">
            Duration (minutes)
          </label>
          <input
            id="activity-minutes"
            type="number"
            inputMode="numeric"
            min={1}
            max={1440}
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
            placeholder="e.g. 45"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
        </div>

        <div>
          <div className="flex items-baseline justify-between gap-2 mb-1">
            <span id="activity-effort" className="block text-sm font-medium text-gray-700">
              How hard was it?
            </span>
            <span className="text-sm font-semibold text-amber-600" aria-hidden="true">
              {labels[effort]}
            </span>
          </div>
          <div className="grid grid-cols-5 gap-2" role="group" aria-labelledby="activity-effort">
            {EFFORT_LEVELS.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={effort === n}
                aria-label={`${n} of 5 — ${labels[n]}`}
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
        </div>

        <div>
          <label htmlFor="activity-notes" className="block text-sm font-medium text-gray-700 mb-1">
            Notes <span className="font-normal text-gray-400">optional</span>
          </label>
          <textarea
            id="activity-notes"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="How did it feel?"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
        </div>

        {error && (
          <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={close}
            className="flex-1 rounded-xl border border-gray-300 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSave || saving}
            className="flex-1 rounded-xl bg-amber-500 py-2.5 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save activity'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
