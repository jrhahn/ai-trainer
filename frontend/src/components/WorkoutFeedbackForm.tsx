import { useState } from 'react'
import type { TrainingDay, WorkoutFeedback } from '../store/useAppStore'
import { EFFORT_LEVELS, effortLabelsFor } from '../utils/effort'

interface Props {
  day: TrainingDay
  onSubmit: (feedback: WorkoutFeedback) => void
  onCancel: () => void
}

export default function WorkoutFeedbackForm({ day, onSubmit, onCancel }: Props) {
  const isStrength = day.workoutType === 'strength'
  const isRideLike = !isStrength
  const effortCopy = effortLabelsFor(day)
  const title = isStrength ? 'Log Strength Session' : 'Log Workout'
  const notesPlaceholder = isStrength
    ? 'Exercises, sets, load, soreness, or anything the coach should know'
    : 'How did it feel? Any issues?'

  const [form, setForm] = useState({
    actualDurationMinutes: day.durationMinutes,
    averagePower: '',
    averageHeartRate: '',
    peakPower: '',
    perceivedEffort: 3 as 1 | 2 | 3 | 4 | 5,
    notes: '',
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({
      actualDurationMinutes: Number(form.actualDurationMinutes),
      averagePower: form.averagePower ? Number(form.averagePower) : undefined,
      averageHeartRate: form.averageHeartRate ? Number(form.averageHeartRate) : undefined,
      peakPower: form.peakPower ? Number(form.peakPower) : undefined,
      perceivedEffort: form.perceivedEffort,
      notes: form.notes,
      completedAt: new Date().toISOString(),
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-lg font-bold text-gray-900 mb-4">{title}</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Duration (minutes) *
              </label>
              <input
                type="number"
                min={1}
                required
                value={form.actualDurationMinutes}
                onChange={(e) => setForm({ ...form, actualDurationMinutes: Number(e.target.value) })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              />
            </div>
            {isRideLike ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Avg Power (W)</label>
                  <input
                    type="number"
                    min={0}
                    value={form.averagePower}
                    onChange={(e) => setForm({ ...form, averagePower: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    placeholder="optional"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Avg HR (bpm)</label>
                  <input
                    type="number"
                    min={0}
                    value={form.averageHeartRate}
                    onChange={(e) => setForm({ ...form, averageHeartRate: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                    placeholder="optional"
                  />
                </div>
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Avg HR (bpm)</label>
                <input
                  type="number"
                  min={0}
                  value={form.averageHeartRate}
                  onChange={(e) => setForm({ ...form, averageHeartRate: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                  placeholder="optional"
                />
              </div>
            )}
            {isRideLike && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Peak Power (W)</label>
                <input
                  type="number"
                  min={0}
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
                  Perceived Effort *
                </span>
                {/* `aria-hidden`: the chosen button already announces its own
                    word, and `aria-pressed` says which one is chosen. Reading
                    this too would say it twice. */}
                <span className="text-sm font-semibold text-amber-600" aria-hidden="true">
                  {effortCopy[form.perceivedEffort]}
                </span>
              </div>
              <div className="grid grid-cols-5 gap-2" role="group" aria-labelledby="effort-label">
                {EFFORT_LEVELS.map((n) => (
                  <button
                    type="button"
                    key={n}
                    onClick={() => setForm({ ...form, perceivedEffort: n })}
                    aria-pressed={form.perceivedEffort === n}
                    aria-label={`${n} of 5 — ${effortCopy[n]}`}
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
                <span>{effortCopy[1]}</span>
                <span>{effortCopy[5]}</span>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
              <textarea
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                rows={3}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                placeholder={notesPlaceholder}
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={onCancel}
                className="flex-1 border border-gray-300 text-gray-700 rounded-lg py-2 text-sm font-medium hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="flex-1 bg-amber-500 text-white rounded-lg py-2 text-sm font-semibold hover:bg-amber-600"
              >
                Save Workout
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
