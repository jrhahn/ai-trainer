import { useState } from 'react'
import Modal from './Modal'
import { useAppStore } from '../store/useAppStore'
import type { RaceEvent } from '../store/useAppStore'
import { createRaceEvent } from '../services/user'
import { useT } from '../i18n'
import { button, input, note } from './ui'

const label = 'block text-sm font-medium text-gray-700'
const unit = 'pointer-events-none absolute right-3 top-2 text-sm text-gray-500'

/**
 * Add a race to the calendar (ai-trainer-ops#175): from "+ Add race" on the
 * dashboard, or from the chat's offer, which brings the date it read. The race
 * is what the plan is built toward, and the plan rebuilds itself toward it
 * (ai-trainer-ops#206).
 *
 * Labels above plain fields, no icons inside them, one grey button (owner's
 * review, 2026-10-24).
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

  return (
    <Modal open onClose={onClose} title={t('Add race')}>
      <div className="mx-auto max-w-lg space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={label}>
            {t('Date')}
            <input
              type="date"
              value={form.date}
              onChange={(e) => setForm((prev) => ({ ...prev, date: e.target.value }))}
              className={`mt-1.5 ${input}`}
            />
          </label>
          <label className={label}>
            {t('Time')}
            <input
              type="time"
              value={form.startTime}
              onChange={(e) => setForm((prev) => ({ ...prev, startTime: e.target.value }))}
              className={`mt-1.5 ${input}`}
            />
          </label>
          <label className={label}>
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
          <label className={label}>
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
        <p className="text-sm text-gray-500">
          {t('Once you add it, your plan is rebuilt toward this race. Done and logged sessions stay.')}
        </p>
        {error && (
          <p role="alert" className={note(false)}>
            {t(error)}
          </p>
        )}
        <div className="flex justify-end">
          <button type="button" onClick={() => void save()} disabled={!canSave} className={button}>
            {t('Add race')}
          </button>
        </div>
      </div>
    </Modal>
  )
}
