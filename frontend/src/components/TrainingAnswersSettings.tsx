import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { Sport, UserProfile, Weekday } from '../store/useAppStore'
import { clearProfileRaceRemote, fetchCurrentUser, updateCurrentUser } from '../services/user'
import { useSaveStatus } from '../hooks/useSaveStatus'
import SaveStatus from './SaveStatus'
import { useT } from '../i18n'

/**
 * The onboarding answers, changeable later (ai-trainer-ops#202).
 *
 * The welcome step promises that every answer can be changed in the chat or in
 * Settings. The chat offered most of them; Settings had none of the four. Each
 * saves itself, like every other setting (#93), with the same profile write the
 * chat's offer makes, so the plan follows the same way: the rebuild compares
 * what the plan was built from with the profile (#158).
 */

const SPORTS: { value: Sport; label: string }[] = [
  { value: 'cycling', label: 'Cycling' },
  { value: 'running', label: 'Running' },
  { value: 'strength', label: 'Strength' },
]

const WEEKDAYS: { value: Weekday; label: string }[] = [
  { value: 'monday', label: 'Mon' },
  { value: 'tuesday', label: 'Tue' },
  { value: 'wednesday', label: 'Wed' },
  { value: 'thursday', label: 'Thu' },
  { value: 'friday', label: 'Fri' },
  { value: 'saturday', label: 'Sat' },
  { value: 'sunday', label: 'Sun' },
]

const LEVELS: { value: UserProfile['fitnessLevel']; label: string }[] = [
  { value: 'beginner', label: 'Beginner' },
  { value: 'intermediate', label: 'Intermediate' },
  { value: 'advanced', label: 'Advanced' },
]

function toggled<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

/** In the week's order, whatever order they were tapped in. */
function inWeekOrder(days: Weekday[]): Weekday[] {
  return WEEKDAYS.map((d) => d.value).filter((d) => days.includes(d))
}

const chip = (on: boolean) =>
  `rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
    on ? 'border-amber-500 bg-amber-50 text-amber-800' : 'border-gray-200 bg-white text-gray-600 hover:border-amber-300'
  }`

export default function TrainingAnswersSettings() {
  const t = useT()
  const { authToken, profile, setUserProfile } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, profile: s.userProfile, setUserProfile: s.setUserProfile }))
  )
  const sportsStatus = useSaveStatus()
  const daysStatus = useSaveStatus()
  const raceStatus = useSaveStatus()
  const levelStatus = useSaveStatus()
  // A race being entered: shown before it has a date, which is when it is saved.
  const [raceOpen, setRaceOpen] = useState(false)
  const [raceDateDraft, setRaceDateDraft] = useState<string | null>(null)
  const [raceDescriptionDraft, setRaceDescriptionDraft] = useState<string | null>(null)

  if (!profile) return null

  const sports = profile.sports?.length ? profile.sports : (['cycling'] as Sport[])
  const days = profile.trainingDays ?? []
  const hasRace = profile.trainingGoal === 'race' && Boolean(profile.raceDate)
  const showRace = hasRace || raceOpen
  const raceDate = raceDateDraft ?? profile.raceDate ?? ''
  const raceDescription = raceDescriptionDraft ?? profile.raceDescription ?? ''

  const save = (status: ReturnType<typeof useSaveStatus>, updates: Parameters<typeof updateCurrentUser>[1]) =>
    status.run(async () => {
      if (!authToken) throw new Error('Please sign in again.')
      const updated = await updateCurrentUser(authToken, updates)
      setUserProfile(updated.profile)
    })

  const toggleSport = (value: Sport) => {
    const next = toggled(sports, value)
    if (next.length === 0) return sportsStatus.fail('Keep at least one sport.')
    void save(sportsStatus, { sports: SPORTS.map((s) => s.value).filter((s) => next.includes(s)) })
  }

  const toggleDay = (value: Weekday) => {
    const next = toggled(days, value)
    if (next.length === 0) return daysStatus.fail('Keep at least one training day.')
    void save(daysStatus, { trainingDays: inWeekOrder(next) })
  }

  const saveRace = (date: string, description: string) => {
    if (!date) return raceStatus.fail('Enter the race date.')
    void save(raceStatus, {
      trainingGoal: 'race',
      raceDate: date,
      raceDescription: description.trim() || undefined,
    }).then((ok) => {
      // A draft goes once its value is stored, unless the athlete typed more
      // while the save was on its way: that is a new edit, saved on its blur.
      if (ok) {
        setRaceDateDraft((current) => (current === date ? null : current))
        setRaceDescriptionDraft((current) => (current === description ? null : current))
      }
    })
  }

  // "No race" is the chat's "I have no race" (ai-trainer-ops#71): upcoming
  // calendar races go too, or the next sync would bring the race back.
  const clearRace = () => {
    setRaceOpen(false)
    setRaceDateDraft(null)
    setRaceDescriptionDraft(null)
    if (!hasRace) return
    void raceStatus.run(async () => {
      if (!authToken) throw new Error('Please sign in again.')
      await clearProfileRaceRemote(authToken)
      const updated = await fetchCurrentUser(authToken)
      setUserProfile(updated.profile)
    })
  }

  return (
    <section
      aria-labelledby="settings-training-heading"
      className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6 space-y-5"
    >
      <div>
        <h3 id="settings-training-heading" className="text-base font-bold text-gray-900 mb-1">
          {t('Your training')}
        </h3>
        <p className="text-xs text-gray-500">
          {t('What you answered when you started. Change it here or tell the coach in the chat; your plan follows.')}
        </p>
      </div>

      <div>
        <p id="settings-sports" className="text-xs font-semibold text-gray-700 mb-2">{t('Sports')}</p>
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby="settings-sports">
          {SPORTS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              aria-pressed={sports.includes(value)}
              onClick={() => toggleSport(value)}
              className={chip(sports.includes(value))}
            >
              {t(label)}
            </button>
          ))}
        </div>
        <SaveStatus state={sportsStatus.state} />
      </div>

      <div>
        <p id="settings-training-days" className="text-xs font-semibold text-gray-700 mb-2">{t('Training days')}</p>
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby="settings-training-days">
          {WEEKDAYS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              aria-pressed={days.includes(value)}
              onClick={() => toggleDay(value)}
              className={chip(days.includes(value))}
            >
              {t(label)}
            </button>
          ))}
        </div>
        <SaveStatus state={daysStatus.state} />
      </div>

      <div>
        <p id="settings-race" className="text-xs font-semibold text-gray-700 mb-2">{t('Race')}</p>
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby="settings-race">
          <button type="button" aria-pressed={showRace} onClick={() => setRaceOpen(true)} className={chip(showRace)}>
            {t('Training for a race')}
          </button>
          <button type="button" aria-pressed={!showRace} onClick={clearRace} className={chip(!showRace)}>
            {t('No race')}
          </button>
        </div>
        {showRace && (
          <div className="mt-3 grid gap-3 sm:grid-cols-[auto_1fr]">
            <div>
              <label htmlFor="settings-race-date" className="block text-xs text-gray-600 mb-1">{t('Race Date')}</label>
              <input
                id="settings-race-date"
                type="date"
                value={raceDate}
                onChange={(e) => {
                  setRaceDateDraft(e.target.value)
                  raceStatus.clearError()
                  if (e.target.value) saveRace(e.target.value, raceDescription)
                }}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="settings-race-description" className="block text-xs text-gray-600 mb-1">
                {t('Race Description')}
              </label>
              <input
                id="settings-race-description"
                type="text"
                value={raceDescription}
                onChange={(e) => setRaceDescriptionDraft(e.target.value)}
                onBlur={() => {
                  if (raceDescriptionDraft != null && raceDescriptionDraft !== (profile.raceDescription ?? '')) {
                    saveRace(raceDate, raceDescriptionDraft)
                  }
                }}
                placeholder={t('e.g. 80km gran fondo with 2000m climbing')}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
              />
            </div>
          </div>
        )}
        <SaveStatus state={raceStatus.state} />
      </div>

      <div>
        <p id="settings-level" className="text-xs font-semibold text-gray-700 mb-2">{t('Level')}</p>
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby="settings-level">
          {LEVELS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              aria-pressed={profile.fitnessLevel === value}
              onClick={() => {
                if (profile.fitnessLevel !== value) void save(levelStatus, { fitnessLevel: value })
              }}
              className={chip(profile.fitnessLevel === value)}
            >
              {t(label)}
            </button>
          ))}
        </div>
        <SaveStatus state={levelStatus.state} />
      </div>
    </section>
  )
}
