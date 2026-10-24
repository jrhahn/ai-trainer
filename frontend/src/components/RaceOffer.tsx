import { useState } from 'react'
import { useShallow } from 'zustand/shallow'
import AddRaceDialog from './AddRaceDialog'
import { button, note, textButton } from './ui'
import { localeTag, useLanguage, useT } from '../i18n'
import { parseLocalDate } from '../utils/workout'
import { updateCurrentUser, updateRaceEventRemote } from '../services/user'
import type { RaceMovedFrom } from '../services/ai'
import { useAppStore } from '../store/useAppStore'

type State = 'asking' | 'open' | 'added' | 'moving' | 'moved' | 'failed' | 'dismissed'

/**
 * Under a reply to "I have a race in 3 weeks": add it to the calendar?
 * (ai-trainer-ops#175). The server read the date; it is shown so the athlete
 * can see what "in 3 weeks" became, and Add opens the race form with it, where
 * they can change it and give the course. Nothing is saved before that, and the
 * plan is rebuilt only from the dashboard card, when the athlete asks.
 *
 * When the athlete said the race moves, the offer moves the race they have,
 * the profile's or the calendar's, instead of adding a second one (#175,
 * comment 5111). The course stays as it is.
 */
export default function RaceOffer({ date, replaces }: { date: string; replaces?: RaceMovedFrom | null }) {
  const t = useT()
  const language = useLanguage()
  const [state, setState] = useState<State>('asking')
  const { authToken, raceEvents, setUserProfile, updateRaceEvent } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      raceEvents: s.raceEvents,
      setUserProfile: s.setUserProfile,
      updateRaceEvent: s.updateRaceEvent,
    })),
  )

  if (state === 'dismissed') return null
  if (state === 'added' || state === 'moved') {
    return (
      <p className={`mt-2 ${note(true)}`}>
        {state === 'added'
          ? t('Added. Your plan is rebuilt toward it.')
          : t('Moved. Your plan is rebuilt toward it.')}
      </p>
    )
  }

  const shown = (iso: string) =>
    parseLocalDate(iso).toLocaleDateString(localeTag(language), {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })

  const move = async () => {
    if (!authToken || !replaces) return
    setState('moving')
    try {
      if (replaces.kind === 'profile') {
        const user = await updateCurrentUser(authToken, { raceDate: date })
        setUserProfile(user.profile)
      } else {
        const event = raceEvents.find((e) => e.id === replaces.eventId)
        if (!event) throw new Error('The race is not in the calendar any more.')
        updateRaceEvent(
          await updateRaceEventRemote(authToken, event.id, {
            date,
            startTime: event.startTime ?? null,
            distanceKm: event.distanceKm,
            elevationM: event.elevationM,
          }),
        )
      }
      setState('moved')
    } catch {
      setState('failed')
    }
  }

  return (
    <div className="mt-2 border-t border-gray-100 pt-2">
      <p className="font-medium text-gray-900">
        {replaces
          ? t('Move your race from {from} to {to}?', { from: shown(replaces.date), to: shown(date) })
          : t('Add a race on {date} to your calendar?', { date: shown(date) })}
      </p>
      <div className="mt-2 flex items-center gap-3">
        <button
          type="button"
          onClick={() => (replaces ? void move() : setState('open'))}
          disabled={state === 'moving'}
          className={button}
        >
          {replaces ? t('Move') : t('Add')}
        </button>
        <button type="button" onClick={() => setState('dismissed')} className={textButton}>
          {t('No thanks')}
        </button>
      </div>
      {state === 'failed' && (
        <p role="alert" className={`mt-1 ${note(false)}`}>
          {t('Could not move the race. Try again later.')}
        </p>
      )}
      {state === 'open' && (
        <AddRaceDialog
          date={date}
          onSaved={() => setState('added')}
          onClose={() => setState((now) => (now === 'added' ? now : 'asking'))}
        />
      )}
    </div>
  )
}
