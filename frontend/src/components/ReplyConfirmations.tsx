import { useState } from 'react'
import { ArrowLeftRight, CalendarCheck, Check, Flag, SlidersHorizontal, Target, Thermometer, X } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  clearProfileRaceRemote,
  fetchCurrentUser,
  removeConstraintRemote,
  removeDayPreferenceRemote,
  removeGoalRemote,
  removeStateRemote,
  updateCurrentUser,
} from '../services/user'
import type { ChatMessage } from '../store/useAppStore'
import { useT, type Translate } from '../i18n'

// The confirm step under a coach reply (ai-trainer-ops#65, #71). A goal said in
// chat is stored at once, so the athlete can keep it or take it back; "I have no
// race" only proposes clearing the profile's race, which needs a press. Both
// decisions are the owner's (ops#63, Q20 and Q21). To correct a goal, the
// athlete says the new target: the newer one replaces it. An illness or a tired
// day (#18) works like a goal: stored at once, undone with Remove. So does a day
// off or a long ride the chat pinned (#110), which every plan write keeps to.
type GoalState = 'open' | 'kept' | 'removed' | 'failed'
type RaceState = 'open' | 'kept' | 'cleared' | 'failed'
type ProfileState = 'open' | 'kept' | 'updated' | 'failed'

const capitalize = (word: string) => word.charAt(0).toUpperCase() + word.slice(1)

// What the reply offers to change, in the athlete's terms (ai-trainer-ops#43).
// The server sends weekdays and sports as lower-case English words; the names
// shown are the interface's (ai-trainer-ops#121). The keys (Monday, Cycling …)
// are shared with the rest of the interface, so a change to one changes it here.
function describeProfileChange(t: Translate, proposal: NonNullable<ChatMessage['profileProposal']>): string {
  const parts: string[] = []
  if (proposal.weeklyHours != null) parts.push(t('{hours} h a week', { hours: proposal.weeklyHours }))
  if (proposal.trainingDays?.length) {
    const days = proposal.trainingDays.map((d) => t(capitalize(d))).join(', ')
    // A day generally preferred, swapped in: it holds from now on (ai-trainer-ops#204).
    parts.push(
      proposal.standingDaySwap ? t('train on {days} from now on', { days }) : t('train on {days}', { days }),
    )
  }
  if (proposal.sports?.length) {
    parts.push(t('sports: {sports}', { sports: proposal.sports.map((s) => t(capitalize(s))).join(', ') }))
  }
  // Offered, not set (ai-trainer-ops#129): it moves every power target in the plan.
  if (proposal.ftp != null) parts.push(t('FTP {watts} W', { watts: proposal.ftp }))
  // Every onboarding answer can be changed in chat (ai-trainer-ops#202).
  if (proposal.fitnessLevel) parts.push(t('level: {level}', { level: t(capitalize(proposal.fitnessLevel)) }))
  if (proposal.maxHeartRate != null) parts.push(t('max heart rate {bpm}', { bpm: proposal.maxHeartRate }))
  return parts.join('; ')
}

export default function ReplyConfirmations({ msg }: { msg: ChatMessage }) {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const setUserProfile = useAppStore((s) => s.setUserProfile)
  const [goals, setGoals] = useState<Record<string, GoalState>>({})
  const [states, setStates] = useState<Record<string, GoalState>>({})
  const [constraints, setConstraints] = useState<Record<string, GoalState>>({})
  const [race, setRace] = useState<RaceState>('open')
  const [profileChange, setProfileChange] = useState<ProfileState>('open')
  const [dayPreferences, setDayPreferences] = useState<Record<string, GoalState>>({})

  const captured = msg.capturedGoals ?? []
  const capturedStates = msg.capturedStates ?? []
  const capturedConstraints = msg.capturedConstraints ?? []
  const capturedDayPreferences = msg.capturedDayPreferences ?? []
  const proposal = msg.raceProposal
  const profileProposal = msg.profileProposal
  const profileText = profileProposal ? describeProfileChange(t, profileProposal) : ''
  if (
    captured.length === 0 &&
    capturedStates.length === 0 &&
    capturedConstraints.length === 0 &&
    capturedDayPreferences.length === 0 &&
    !proposal &&
    !profileText
  )
    return null
  const raceLabel = [proposal?.raceDescription, proposal?.raceDate].filter(Boolean).join(', ')
  const calendarRaces = proposal?.calendarRaces ?? 0
  const calendarText =
    calendarRaces > 0
      ? calendarRaces === 1
        ? t(' and the upcoming race in your calendar')
        : t(' and {count} upcoming races in your calendar', { count: calendarRaces })
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

  const removeState = async (id: string) => {
    if (!authToken) return
    try {
      await removeStateRemote(authToken, id)
      setStates((s) => ({ ...s, [id]: 'removed' }))
    } catch {
      setStates((s) => ({ ...s, [id]: 'failed' }))
    }
  }

  // Kept for every plan, shown so the athlete sees what the coach took from it
  // (ai-trainer-ops#204). Removing it also clears it from Settings.
  const removeDayPreference = async (key: string, prefer: string, over: string) => {
    if (!authToken) return
    try {
      await removeDayPreferenceRemote(authToken, prefer, over)
      setDayPreferences((d) => ({ ...d, [key]: 'removed' }))
      const user = await fetchCurrentUser(authToken).catch(() => null)
      if (user) setUserProfile(user.profile)
    } catch {
      setDayPreferences((d) => ({ ...d, [key]: 'failed' }))
    }
  }

  const removeConstraint = async (id: string) => {
    if (!authToken) return
    try {
      await removeConstraintRemote(authToken, id)
      setConstraints((c) => ({ ...c, [id]: 'removed' }))
    } catch {
      setConstraints((c) => ({ ...c, [id]: 'failed' }))
    }
  }

  const applyProfileChange = async () => {
    if (!authToken || !profileProposal) return
    try {
      const user = await updateCurrentUser(authToken, {
        weeklyHours: profileProposal.weeklyHours ?? undefined,
        trainingDays: profileProposal.trainingDays ?? undefined,
        sports: profileProposal.sports ?? undefined,
        currentFTP: profileProposal.ftp ?? undefined,
        fitnessLevel: profileProposal.fitnessLevel ?? undefined,
        maxHeartRate: profileProposal.maxHeartRate ?? undefined,
      })
      setProfileChange('updated')
      setUserProfile(user.profile)
    } catch {
      setProfileChange('failed')
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
                {t('Goal noted:')} <strong>{goal.label}</strong>
              </span>
            </p>
            {state === 'open' && (
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => setGoals((g) => ({ ...g, [goal.id]: 'kept' }))}
                >
                  <Check size={11} aria-hidden="true" /> {t('Keep')}
                </button>
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => void removeGoal(goal.id)}
                >
                  <X size={11} aria-hidden="true" /> {t('Remove')}
                </button>
                <span className="text-gray-400">{t('To change it, just tell me the new target.')}</span>
              </div>
            )}
            {state === 'kept' && <p className="pl-4 text-gray-400">{t('Kept.')}</p>}
            {state === 'removed' && <p className="pl-4 text-gray-400">{t('Removed.')}</p>}
            {state === 'failed' && <p className="pl-4 text-red-600">{t('Could not remove it. Try again later.')}</p>}
          </div>
        )
      })}
      {capturedStates.map((item) => {
        const state = states[item.id] ?? 'open'
        return (
          <div key={item.id} className="text-xs text-gray-600">
            <p className="flex items-center gap-1">
              <Thermometer size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
              <span>
                {t('Noted:')} <strong>{t(item.label)}</strong>
              </span>
            </p>
            {item.removable && state === 'open' && (
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => setStates((s) => ({ ...s, [item.id]: 'kept' }))}
                >
                  <Check size={11} aria-hidden="true" /> {t('Keep')}
                </button>
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => void removeState(item.id)}
                >
                  <X size={11} aria-hidden="true" /> {t('Remove')}
                </button>
                <span className="text-gray-400">{t('If it changes, just tell me.')}</span>
              </div>
            )}
            {state === 'kept' && <p className="pl-4 text-gray-400">{t('Kept.')}</p>}
            {state === 'removed' && <p className="pl-4 text-gray-400">{t('Removed.')}</p>}
            {state === 'failed' && <p className="pl-4 text-red-600">{t('Could not remove it. Try again later.')}</p>}
          </div>
        )
      })}
      {capturedConstraints.map((item) => {
        const state = constraints[item.id] ?? 'open'
        return (
          <div key={item.id} className="text-xs text-gray-600">
            <p className="flex items-center gap-1">
              <CalendarCheck size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
              <span>
                {t('Pinned for every plan:')} <strong>{item.label}</strong>
              </span>
            </p>
            {item.removable && state === 'open' && (
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => setConstraints((c) => ({ ...c, [item.id]: 'kept' }))}
                >
                  <Check size={11} aria-hidden="true" /> {t('Keep')}
                </button>
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => void removeConstraint(item.id)}
                >
                  <X size={11} aria-hidden="true" /> {t('Remove')}
                </button>
              </div>
            )}
            {state === 'kept' && <p className="pl-4 text-gray-400">{t('Kept.')}</p>}
            {state === 'removed' && <p className="pl-4 text-gray-400">{t('Removed.')}</p>}
            {state === 'failed' && <p className="pl-4 text-red-600">{t('Could not remove it. Try again later.')}</p>}
          </div>
        )
      })}
      {capturedDayPreferences.map((item) => {
        const key = `${item.prefer}>${item.over}`
        const state = dayPreferences[key] ?? 'open'
        return (
          <div key={key} className="text-xs text-gray-600">
            <p className="flex items-center gap-1">
              <ArrowLeftRight size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
              <span>
                {t('Preferred day, for every plan:')}{' '}
                <strong>
                  {t('{prefer} rather than {over}', {
                    prefer: t(capitalize(item.prefer)),
                    over: t(capitalize(item.over)),
                  })}
                </strong>
              </span>
            </p>
            {state === 'open' && (
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-4">
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => setDayPreferences((d) => ({ ...d, [key]: 'kept' }))}
                >
                  <Check size={11} aria-hidden="true" /> {t('Keep')}
                </button>
                <button
                  type="button"
                  className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                  onClick={() => void removeDayPreference(key, item.prefer, item.over)}
                >
                  <X size={11} aria-hidden="true" /> {t('Remove')}
                </button>
                <span className="text-gray-400">{t('The key session goes there first.')}</span>
              </div>
            )}
            {state === 'kept' && <p className="pl-4 text-gray-400">{t('Kept.')}</p>}
            {state === 'removed' && <p className="pl-4 text-gray-400">{t('Removed.')}</p>}
            {state === 'failed' && <p className="pl-4 text-red-600">{t('Could not remove it. Try again later.')}</p>}
          </div>
        )
      })}
      {profileText && (
        <div className="text-xs text-gray-600">
          <p className="flex items-center gap-1">
            <SlidersHorizontal size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
            <span>
              {t('Update your profile:')} <strong>{profileText}</strong>?
            </span>
          </p>
          {profileChange === 'open' && (
            <div className="mt-1 flex flex-wrap gap-2 pl-4">
              <button
                type="button"
                className={`${button} border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100`}
                onClick={() => void applyProfileChange()}
              >
                <Check size={11} aria-hidden="true" /> {t('Update profile')}
              </button>
              <button
                type="button"
                className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                onClick={() => setProfileChange('kept')}
              >
                <X size={11} aria-hidden="true" /> {t('Leave it')}
              </button>
            </div>
          )}
          {profileChange === 'kept' && <p className="pl-4 text-gray-400">{t('Left as it was.')}</p>}
          {profileChange === 'updated' && <p className="pl-4 text-gray-400">{t('Updated. The coach plans with it from now on.')}</p>}
          {profileChange === 'failed' && <p className="pl-4 text-red-600">{t('Could not update it. Try again later.')}</p>}
        </div>
      )}
      {proposal && (
        <div className="text-xs text-gray-600">
          <p className="flex items-center gap-1">
            <Flag size={12} aria-hidden="true" className="shrink-0 text-amber-600" />
            <span>
              {t('Remove the race from your profile')}{calendarText}
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
                <X size={11} aria-hidden="true" /> {t('Remove race')}
              </button>
              <button
                type="button"
                className={`${button} border-gray-300 text-gray-700 hover:bg-white`}
                onClick={() => setRace('kept')}
              >
                <Check size={11} aria-hidden="true" /> {t('Keep it')}
              </button>
            </div>
          )}
          {race === 'kept' && <p className="pl-4 text-gray-400">{t('Kept.')}</p>}
          {race === 'cleared' && <p className="pl-4 text-gray-400">{t('Removed. The coach now plans without a race.')}</p>}
          {race === 'failed' && <p className="pl-4 text-red-600">{t('Could not remove it. Try again later.')}</p>}
        </div>
      )}
    </div>
  )
}
