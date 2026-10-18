import { useState } from 'react'
import type { TrainingDay, WorkoutFeedback } from '../store/useAppStore'
import { EFFORT_LEVELS, effortLabelsFor } from '../utils/effort'
import { useT } from '../i18n'

interface Props {
  day: TrainingDay
  /** May return the save. The form then stays open until it lands, and says so
   *  when it fails, so the athlete can save again (ai-trainer-ops#127). */
  onSubmit: (feedback: WorkoutFeedback) => void | Promise<void>
  onCancel: () => void
}

// The server's bounds for a logged session (ai-trainer-ops#127, app #940): a
// value outside them is a 422, so the form refuses it before it is sent.
const MAX_MINUTES = 1440
const MIN_HEART_RATE = 30
const MAX_HEART_RATE = 250
const MAX_AVERAGE_POWER = 2500
const MAX_PEAK_POWER = 3000
const MAX_NOTES = 2000

export default function WorkoutFeedbackForm({ day, onSubmit, onCancel }: Props) {
  const t = useT()
  const isStrength = day.workoutType === 'strength'
  const isRideLike = !isStrength
  const effortCopy = effortLabelsFor(day)
  const title = isStrength ? t('Log Strength Session') : t('Log Workout')
  const notesPlaceholder = isStrength
    ? t('Exercises, sets, load, soreness, or anything the coach should know')
    : t('How did it feel? Any issues?')

  const [form, setForm] = useState({
    actualDurationMinutes: day.durationMinutes,
    averagePower: '',
    averageHeartRate: '',
    peakPower: '',
    perceivedEffort: 3 as 1 | 2 | 3 | 4 | 5,
    notes: '',
  })

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      await onSubmit({
        actualDurationMinutes: Number(form.actualDurationMinutes),
        averagePower: form.averagePower ? Number(form.averagePower) : undefined,
        averageHeartRate: form.averageHeartRate ? Number(form.averageHeartRate) : undefined,
        peakPower: form.peakPower ? Number(form.peakPower) : undefined,
        perceivedEffort: form.perceivedEffort,
        notes: form.notes,
        completedAt: new Date().toISOString(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-lg font-bold text-gray-900 mb-4">{title}</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('Duration (minutes) *')}
              </label>
              <input
                type="number"
                min={1}
                max={MAX_MINUTES}
                required
                value={form.actualDurationMinutes}
                onChange={(e) => setForm({ ...form, actualDurationMinutes: Number(e.target.value) })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              />
            </div>
            {isRideLike ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('Avg Power (W)')}</label>
                  <input
                    type="number"
                    min={0}
                    max={MAX_AVERAGE_POWER}
                    value={form.averagePower}
                    onChange={(e) => setForm({ ...form, averagePower: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    placeholder="optional"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('Avg HR (bpm)')}</label>
                  <input
                    type="number"
                    min={MIN_HEART_RATE}
                    max={MAX_HEART_RATE}
                    value={form.averageHeartRate}
                    onChange={(e) => setForm({ ...form, averageHeartRate: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    placeholder="optional"
                  />
                </div>
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('Avg HR (bpm)')}</label>
                <input
                  type="number"
                  min={MIN_HEART_RATE}
                  max={MAX_HEART_RATE}
                  value={form.averageHeartRate}
                  onChange={(e) => setForm({ ...form, averageHeartRate: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  placeholder="optional"
                />
              </div>
            )}
            {isRideLike && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('Peak Power (W)')}</label>
                <input
                  type="number"
                  min={0}
                  max={MAX_PEAK_POWER}
                  value={form.peakPower}
                  onChange={(e) => setForm({ ...form, peakPower: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  placeholder="optional"
                />
              </div>
            )}
            {/* The effort scale, laid out so no label can push it out of shape
                (ai-trainer-ops#51.5).

                Each button used to carry its own word under the number, on
                `flex-1`, which sizes to content: at 390 px "Moderate" made its
                button 59 px against the others' 55, "Very Hard" wrapped to two
                lines, and the row came to 311 px inside a 310 px dialog. The
                dialog is `max-w-md`, so the five columns are never wider than
                ~74 px on any screen — and the strength wording ("Controlled",
                "Challenging") needs more than that. There was no width at which
                this layout worked; it only looked worse on a phone.

                So the buttons hold the number and nothing else, on a fixed
                five-column grid: no label length can reach the layout. The word
                is still said, three ways — the current choice in full beside the
                field label, the two ends of the scale underneath, and every
                level in its own button's accessible name. */}
            <div>
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span id="effort-label" className="block text-sm font-medium text-gray-700">
                  {t('Perceived Effort *')}
                </span>
                {/* `aria-hidden`: the chosen button already announces its own
                    word, and `aria-pressed` says which one is chosen. Reading
                    this too would say it twice. */}
                <span className="text-sm font-semibold text-amber-600" aria-hidden="true">
                  {t(effortCopy[form.perceivedEffort])}
                </span>
              </div>
              <div className="grid grid-cols-5 gap-2" role="group" aria-labelledby="effort-label">
                {EFFORT_LEVELS.map((n) => (
                  <button
                    type="button"
                    key={n}
                    onClick={() => setForm({ ...form, perceivedEffort: n })}
                    aria-pressed={form.perceivedEffort === n}
                    aria-label={t('{n} of 5 — {label}', { n, label: t(effortCopy[n]) })}
                    className={`rounded-lg border py-2.5 text-sm font-semibold transition-colors ${
                      form.perceivedEffort === n
                        ? 'bg-amber-500 border-amber-500 text-white'
                        : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              {/* The ends of the scale, so 1 and 5 are readable without being
                  selected first. `aria-hidden` for the same reason as above. */}
              <div
                className="mt-1 flex justify-between px-0.5 text-[11px] text-gray-500"
                aria-hidden="true"
              >
                <span>{t(effortCopy[1])}</span>
                <span>{t(effortCopy[5])}</span>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('Notes')}</label>
              <textarea
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                rows={3}
                maxLength={MAX_NOTES}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                placeholder={notesPlaceholder}
              />
            </div>
            {error && (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                <p className="font-medium">{t('The session was not saved.')}</p>
                <p>{t(error)}</p>
              </div>
            )}
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={onCancel}
                className="flex-1 border border-gray-300 text-gray-700 rounded-lg py-2 text-sm font-medium hover:bg-gray-50"
              >
                {t('Cancel')}
              </button>
              <button
                type="submit"
                disabled={saving}
                className="flex-1 bg-amber-500 text-white rounded-lg py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-60"
              >
                {saving ? t('Saving…') : error ? t('Try again') : t('Save Workout')}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
