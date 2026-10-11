import { useState } from 'react'
import { Check, Flag, Target, X } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { clearProfileRaceRemote, fetchCurrentUser, removeGoalRemote } from '../services/user'
import type { ChatMessage } from '../store/useAppStore'

// The confirm step under a coach reply (ai-trainer-ops#65, #71). A goal said in
// chat is stored at once, so the athlete can keep it or take it back; "I have no
// race" only proposes clearing the profile's race, which needs a press. Both
// decisions are the owner's (ops#63, Q20 and Q21). To correct a goal, the
// athlete says the new target: the newer one replaces it.
type GoalState = 'open' | 'kept' | 'removed' | 'failed'
type RaceState = 'open' | 'kept' | 'cleared' | 'failed'

export default function ReplyConfirmations({ msg }: { msg: ChatMessage }) {
  const authToken = useAppStore((s) => s.authToken)
  const setUserProfile = useAppStore((s) => s.setUserProfile)
  const [goals, setGoals] = useState<Record<string, GoalState>>({})
  const [race, setRace] = useState<RaceState>('open')

  const captured = msg.capturedGoals ?? []
  const proposal = msg.raceProposal
  if (captured.length === 0 && !proposal) return null
  const raceLabel = [proposal?.raceDescription, proposal?.raceDate].filter(Boolean).join(', ')
  const calendarRaces = proposal?.calendarRaces ?? 0
  const calendarText =
    calendarRaces > 0
      ? ` and ${calendarRaces === 1 ? 'the upcoming race' : `${calendarRaces} upcoming races`} in your calendar`
      : ''

  const removeGoal = async (id: string) => {
    if (!authToken) return
    try {
      await removeGoalRemote(authToken, id)
      setGoals((g) => ({ ...g, [id]: 'removed' }))
    } catch {
      setGoals((g) => ({ ...g, [id]: 'failed' }))
    }
  }

  const clearRace = async () => {
    if (!authToken) return
    try {
      await clearProfileRaceRemote(authToken)
      setRace('cleared')
      const user = await fetchCurrentUser(authToken).catch(() => null)
      if (user) setUserProfile(user.profile)
    } catch {
      setRace('failed')
    }
  }

  const button =
    'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors'

  return (
    <div className="mt-2 space-y-1.5 border-t border-gray-200 pt-2">
      {captured.map((goal) => {
        const state = goals[goal.id] ?? 'open'
        return (
          <div key={goal.id} className="text-xs text-gray-600">
            <p className="flex items-center gap-1">
              <Target size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
              <span>
                Goal noted: <strong>{goal.label}</strong>
              </span>
            </p>
            {state === 'open' && (
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => setGoals((g) => ({ ...g, [goal.id]: 'kept' }))}
                >
                  <Check size={11} aria-hidden="true" /> Keep
                </button>
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => void removeGoal(goal.id)}
                >
                  <X size={11} aria-hidden="true" /> Remove
                </button>
                <span className="text-gray-400">To change it, just tell me the new target.</span>
              </div>
            )}
            {state === 'kept' && <p className="pl-4 text-gray-400">Kept.</p>}
            {state === 'removed' && <p className="pl-4 text-gray-400">Removed.</p>}
            {state === 'failed' && <p className="pl-4 text-red-600">Could not remove it. Try again later.</p>}
          </div>
        )
      })}
      {proposal && (
        <div className="text-xs text-gray-600">
          <p className="flex items-center gap-1">
            <Flag size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
            <span>
              Remove the race from your profile{calendarText}
              {raceLabel ? ` (${raceLabel})` : ''}?
            </span>
          </p>
          {race === 'open' && (
            <div className="mt-1 flex flex-wrap gap-2 pl-4">
              <button
                type="button"
                className={`${button} border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100`}
                onClick={() => void clearRace()}
              >
                <X size={11} aria-hidden="true" /> Remove race
              </button>
              <button
                type="button"
                className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                onClick={() => setRace('kept')}
              >
                <Check size={11} aria-hidden="true" /> Keep it
              </button>
            </div>
          )}
          {race === 'kept' && <p className="pl-4 text-gray-400">Kept.</p>}
          {race === 'cleared' && <p className="pl-4 text-gray-400">Removed. The coach now plans without a race.</p>}
          {race === 'failed' && <p className="pl-4 text-red-600">Could not remove it. Try again later.</p>}
        </div>
      )}
    </div>
  )
}
