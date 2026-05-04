import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { submitRideFeedback } from '../services/user'
import { useAppStore } from '../store/useAppStore'
import { useShallow } from 'zustand/shallow'

export type LegsFeeling = 'fresh' | 'normal' | 'heavy'
export type RideIntent = 'planned workout' | 'recovery' | 'commute' | 'free ride' | 'aborted'

const legsLabels: Record<LegsFeeling, string> = {
  fresh: '🟢 Fresh',
  normal: '🟡 Normal',
  heavy: '🔴 Heavy',
}

const intentLabels: Record<RideIntent, string> = {
  'planned workout': '📋 Planned workout',
  recovery: '😴 Recovery',
  commute: '🚦 Commute',
  'free ride': '🌄 Free ride',
  aborted: '❌ Aborted',
}

interface Props {
  stravaActivityId: number
  activityDate: string
  onSaved: (userNote: string) => void
  onCancel: () => void
}

export default function RideFeedbackForm({ stravaActivityId, activityDate, onSaved, onCancel }: Props) {
  const { authToken } = useAppStore(useShallow((s) => ({ authToken: s.authToken })))

  const [rpe, setRpe] = useState(5)
  const [legs, setLegs] = useState<LegsFeeling>('normal')
  const [intent, setIntent] = useState<RideIntent>('planned workout')
  const [note, setNote] = useState('')

  const mutation = useMutation({
    mutationFn: () =>
      submitRideFeedback(authToken!, stravaActivityId, {
        rpe,
        legs,
        intent,
        note: note.trim() || undefined,
      }),
    onSuccess: (data) => onSaved(data.userNote),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm max-h-[90vh] overflow-y-auto">
        <div className="p-5">
          <h2 className="text-base font-bold text-gray-900 mb-0.5">How was your ride?</h2>
          <p className="text-xs text-gray-500 mb-4">{activityDate}</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* RPE */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Perceived effort (RPE): <span className="font-bold text-amber-600">{rpe}/10</span>
              </label>
              <input
                type="range"
                min={1}
                max={10}
                value={rpe}
                onChange={(e) => setRpe(Number(e.target.value))}
                className="w-full accent-amber-500"
              />
              <div className="flex justify-between text-xs text-gray-400 mt-0.5">
                <span>Very easy</span>
                <span>Max effort</span>
              </div>
            </div>

            {/* Legs feeling */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Legs</label>
              <div className="flex gap-2">
                {(Object.keys(legsLabels) as LegsFeeling[]).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setLegs(option)}
                    className={`flex-1 py-2 rounded-lg text-xs font-semibold border transition-colors ${
                      legs === option
                        ? 'bg-amber-500 border-amber-500 text-white'
                        : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                    }`}
                  >
                    {legsLabels[option]}
                  </button>
                ))}
              </div>
            </div>

            {/* Intent */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">What was this ride?</label>
              <div className="grid grid-cols-2 gap-2">
                {(Object.keys(intentLabels) as RideIntent[]).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setIntent(option)}
                    className={`py-2 px-2 rounded-lg text-xs font-semibold border transition-colors text-left ${
                      intent === option
                        ? 'bg-amber-500 border-amber-500 text-white'
                        : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                    }`}
                  >
                    {intentLabels[option]}
                  </button>
                ))}
              </div>
            </div>

            {/* Optional note */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Note <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                maxLength={500}
                placeholder="Anything else the coach should know?"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 resize-none"
              />
            </div>

            {mutation.isError && (
              <p className="text-xs text-red-600">
                {mutation.error instanceof Error ? mutation.error.message : 'Failed to save feedback'}
              </p>
            )}

            <div className="flex gap-3 pt-1">
              <button
                type="button"
                onClick={onCancel}
                className="flex-1 border border-gray-300 text-gray-700 rounded-lg py-2 text-sm font-medium hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={mutation.isPending}
                className="flex-1 bg-amber-500 text-white rounded-lg py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {mutation.isPending ? 'Saving…' : 'Save feedback'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
