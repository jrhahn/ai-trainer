import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'
import { saveChatMessage, submitRideFeedback } from '../services/user'
import { useAppStore, type ChatMessage, type RideMetricPoint, type TrainingDay } from '../store/useAppStore'
import { useShallow } from 'zustand/shallow'
import { activityNoun, formatActivityType, isCyclingActivity } from '../utils/activityType'

export type LegsFeeling = 'fresh' | 'normal' | 'heavy'
export type RideIntent = 'planned workout' | 'recovery' | 'commute' | 'free ride' | 'free activity' | 'aborted'
export type PlanMatchFeedback = 'unspecified' | 'matched' | 'mostly_matched' | 'not_matched'

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
  'free activity': '🌄 Free activity',
  aborted: '❌ Aborted',
}

const matchLabels: Record<PlanMatchFeedback, string> = {
  unspecified: 'No override',
  matched: 'Matched plan',
  mostly_matched: 'Partly matched',
  not_matched: 'Off plan',
}

interface Props {
  stravaActivityId: number
  activityDate: string
  activityName?: string | null
  sportType?: string | null
  onSaved: (data: {
    userNote: string
    coachNote?: string | null
    planUpdates?: Partial<TrainingDay>[]
    ride?: RideMetricPoint | null
  }) => void
  onCancel: () => void
}

function rideReference(activityDate: string, activityName?: string | null, sportType?: string | null): string {
  const trimmedName = activityName?.trim()
  const noun = activityNoun(sportType ?? 'Ride')
  return trimmedName ? `your ${noun} "${trimmedName}" on ${activityDate}` : `your ${noun} on ${activityDate}`
}

function buildRideFeedbackChatMessages(
  activityDate: string,
  activityName: string | null | undefined,
  sportType: string | null | undefined,
  data: {
    userNote: string
    coachNote?: string | null
  },
): ChatMessage[] {
  const reference = rideReference(activityDate, activityName, sportType)
  const timestamp = new Date().toISOString()
  const messages: ChatMessage[] = [
    {
      role: 'user',
      content: `Ride feedback for ${reference}: ${data.userNote}`,
      timestamp,
    },
  ]

  const coachNote = data.coachNote?.trim()
  if (coachNote) {
    messages.push({
      role: 'assistant',
      content: `About ${reference}: ${coachNote}`,
      timestamp,
    })
  }

  return messages
}

export default function RideFeedbackForm({ stravaActivityId, activityDate, activityName, sportType, onSaved, onCancel }: Props) {
  const { authToken, addPendingFeedbackRide, addChatMessage } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      addPendingFeedbackRide: s.addPendingFeedbackRide,
      addChatMessage: s.addChatMessage,
    })),
  )

  const [rpe, setRpe] = useState(5)
  const [legs, setLegs] = useState<LegsFeeling>('normal')
  const [intent, setIntent] = useState<RideIntent>('planned workout')
  const [planMatchFeedback, setPlanMatchFeedback] = useState<PlanMatchFeedback>('unspecified')
  const [note, setNote] = useState('')
  const [savedNote, setSavedNote] = useState<string | null>(null)
  const [savedData, setSavedData] = useState<{
    userNote: string
    coachNote?: string | null
    planUpdates?: Partial<TrainingDay>[]
    ride?: RideMetricPoint | null
  } | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      submitRideFeedback(authToken!, stravaActivityId, {
        rpe,
        legs,
        intent,
        planMatchFeedback: planMatchFeedback === 'unspecified' ? undefined : planMatchFeedback,
        note: note.trim() || undefined,
      }),
    onSuccess: (data) => {
      setSavedNote(data.userNote)
      setSavedData(data)
      addPendingFeedbackRide(stravaActivityId)

      const chatMessages = buildRideFeedbackChatMessages(activityDate, activityName, sportType, data)
      chatMessages.forEach((message) => addChatMessage(message))
      void Promise.all(chatMessages.map((message) => saveChatMessage(authToken!, message))).catch((error) => {
        console.warn('Failed to persist ride feedback chat messages:', error)
      })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate()
  }

  const handleClose = () => {
    onSaved(savedData ?? { userNote: savedNote ?? '' })
  }

  const noun = activityNoun(sportType ?? 'Ride')
  const typeLabel = sportType ? formatActivityType(sportType) : ''
  const freeIntent: RideIntent = isCyclingActivity(sportType ?? 'Ride') ? 'free ride' : 'free activity'
  const intentOptions: RideIntent[] = ['planned workout', 'recovery', 'commute', freeIntent, 'aborted']

  // --- Confirmation step (shown after feedback is saved) ---
  if (savedNote !== null) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm">
          <div className="p-5 space-y-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                <CheckCircle2 size={16} className="text-green-600" />
              </div>
              <h2 className="text-base font-bold text-gray-900">Feedback saved!</h2>
            </div>

            <p className="text-sm text-gray-600 leading-relaxed">
              Your training summary will update shortly with coaching insights for this {noun}.
            </p>

            <button
              onClick={handleClose}
              className="w-full bg-amber-500 text-white rounded-lg py-2 text-sm font-semibold hover:bg-amber-600 transition-colors"
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    )
  }

  // --- Feedback input step ---
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm max-h-[90vh] overflow-y-auto">
        <div className="p-5">
          <h2 className="text-base font-bold text-gray-900 mb-0.5">How was your {noun}?</h2>
          <p className="text-xs text-gray-500 mb-4">
            {activityDate}
            {typeLabel ? ` · ${typeLabel}` : ''}
          </p>

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
              <label className="block text-sm font-medium text-gray-700 mb-1">What was this {noun}?</label>
              <div className="grid grid-cols-2 gap-2">
                {intentOptions.map((option) => (
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

            {/* Plan match correction */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Plan match</label>
              <div className="grid grid-cols-2 gap-2">
                {(Object.keys(matchLabels) as PlanMatchFeedback[]).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setPlanMatchFeedback(option)}
                    className={`py-2 px-2 rounded-lg text-xs font-semibold border transition-colors text-left ${
                      planMatchFeedback === option
                        ? 'bg-amber-500 border-amber-500 text-white'
                        : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400'
                    }`}
                  >
                    {matchLabels[option]}
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
