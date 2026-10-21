import { useState } from 'react'
import { Clock, Mountain, Route, Save } from 'lucide-react'
import Modal from './Modal'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent } from '../store/useAppStore'
import { createRaceEvent } from '../services/user'
import { useT } from '../i18n'

/**
 * Add a race to the calendar (ai-trainer-ops#175): from "+ Add race" on the
 * dashboard, or from the chat's offer, which brings the date it read. The race
 * is what the plan is built toward; nothing is rebuilt here. A new race brings
 * up the "Rebuild your plan" card and the athlete decides there.
 */
export default function AddRaceDialog({
  date = '',
  onClose,
  onSaved,
}: {
  /** ISO date to start from, e.g. the one the chat read; the athlete may change it. */
  date?: string
  onClose: () => void
  onSaved?: (event: RaceEvent) => void
}) {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const addRaceEvent = useAppStore((s) => s.addRaceEvent)
  const [form, setForm] = useState({ date, startTime: '', distanceKm: '', elevationM: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSave =
    Boolean(authToken) &&
    Boolean(form.date) &&
    Number(form.distanceKm) > 0 &&
    form.elevationM !== '' &&
    Number(form.elevationM) >= 0 &&
    !saving

  const save = async () => {
    if (!authToken || !canSave) return
    setSaving(true)
    setError(null)
    try {
      const created = await createRaceEvent(authToken, {
        date: form.date,
        startTime: form.startTime || null,
        distanceKm: Number(form.distanceKm),
        elevationM: Math.round(Number(form.elevationM)),
      })
      addRaceEvent(created)
      onSaved?.(created)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save race event')
      setSaving(false)
    }
  }

  const input = 'w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500'

  return (
    <Modal open onClose={onClose} title={t('Add race')}>
      <div className="mx-auto max-w-lg space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-semibold text-gray-600">
            {t('Date')}
            <input
              type="date"
              value={form.date}
              onChange={(e) => setForm((prev) => ({ ...prev, date: e.target.value }))}
              className={`mt-1 ${input}`}
            />
          </label>
          <label className="text-xs font-semibold text-gray-600">
            {t('Time')}
            <div className="relative mt-1">
              <Clock size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
              <input
                type="time"
                value={form.startTime}
                onChange={(e) => setForm((prev) => ({ ...prev, startTime: e.target.value }))}
                className={`pl-9 ${input}`}
              />
            </div>
          </label>
          <label className="text-xs font-semibold text-gray-600">
            {t('Distance')}
            <div className="relative mt-1">
              <Route size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
              <input
                type="number"
                min="0"
                step="0.1"
                value={form.distanceKm}
                onChange={(e) => setForm((prev) => ({ ...prev, distanceKm: e.target.value }))}
                className={`pl-9 pr-10 ${input}`}
              />
              <span className="pointer-events-none absolute right-3 top-2.5 text-xs text-gray-400">km</span>
            </div>
          </label>
          <label className="text-xs font-semibold text-gray-600">
            {t('Climb')}
            <div className="relative mt-1">
              <Mountain size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
              <input
                type="number"
                min="0"
                step="1"
                value={form.elevationM}
                onChange={(e) => setForm((prev) => ({ ...prev, elevationM: e.target.value }))}
                className={`pl-9 pr-10 ${input}`}
              />
              <span className="pointer-events-none absolute right-3 top-2.5 text-xs text-gray-400">m</span>
            </div>
          </label>
        </div>
        <p className="text-xs text-gray-500">
          {t('Your plan is not changed yet: the dashboard then offers to rebuild it toward this race.')}
        </p>
        {error && (
          <p role="alert" className="text-xs font-medium text-red-600">
            {t(error)}
          </p>
        )}
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => void save()}
            disabled={!canSave}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
          >
            <Save size={15} aria-hidden="true" />
            {t('Add race')}
          </button>
        </div>
      </div>
    </Modal>
  )
}
