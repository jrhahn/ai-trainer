import { useState } from 'react'
import type { TrainingDay, WorkoutFeedback } from '../store/useAppStore'

const effortLabels: Record<number, string> = {
  1: 'Easy',
  2: 'Moderate',
  3: 'Hard',
  4: 'Very Hard',
  5: 'Max',
}

const strengthEffortLabels: Record<number, string> = {
  1: 'Easy',
  2: 'Controlled',
  3: 'Challenging',
  4: 'Very Hard',
  5: 'Max',
}

interface Props {
  day: TrainingDay
  onSubmit: (feedback: WorkoutFeedback) => void
  onCancel: () => void
}

export default function WorkoutFeedbackForm({ day, onSubmit, onCancel }: Props) {
  const isStrength = day.workoutType === 'strength'
  const isRideLike = !isStrength
  const effortCopy = isStrength ? strengthEffortLabels : effortLabels
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
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Perceived Effort *
              </label>
              <div className="flex gap-2">
                {([1, 2, 3, 4, 5] as const).map((n) => (
                  <button
                    type="button"
                    key={n}
                    onClick={() => setForm({ ...form, perceivedEffort: n })}
                    className={`flex-1 py-2 rounded-lg text-xs font-semibold border transition-colors ${
                      form.perceivedEffort === n
                        ? 'bg-amber-500 border-amber-500 text-white'
                        : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                    }`}
                  >
                    {n}
                    <br />
                    <span className="font-normal">{effortCopy[n]}</span>
                  </button>
                ))}
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
