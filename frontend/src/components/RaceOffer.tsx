import { useState } from 'react'
import { Flag } from 'lucide-react'
import AddRaceDialog from './AddRaceDialog'
import { localeTag, useLanguage, useT } from '../i18n'
import { parseLocalDate } from '../utils/workout'

/**
 * Under a reply to "I have a race in 3 weeks": add it to the calendar?
 * (ai-trainer-ops#175). The server read the date; it is shown so the athlete
 * can see what "in 3 weeks" became, and Add opens the race form with it, where
 * they can change it and give the course. Nothing is saved before that, and the
 * plan is rebuilt only from the dashboard card, when the athlete asks.
 */
export default function RaceOffer({ date }: { date: string }) {
  const t = useT()
  const language = useLanguage()
  const [state, setState] = useState<'asking' | 'open' | 'added' | 'dismissed'>('asking')

  if (state === 'dismissed') return null
  if (state === 'added') {
    return (
      <p className="mt-2 text-xs font-medium text-green-700">
        {t('Added. The dashboard now offers to rebuild your plan toward it.')}
      </p>
    )
  }

  const shown = parseLocalDate(date).toLocaleDateString(localeTag(language), {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })

  return (
    <div className="mt-2 border-t border-gray-200 pt-2">
      <p className="flex items-center gap-1 text-xs font-semibold text-gray-600">
        <Flag size={12} aria-hidden="true" />
        {t('Add a race on {date} to your calendar?', { date: shown })}
      </p>
      <div className="mt-1.5 flex gap-2">
        <button
          type="button"
          onClick={() => setState('open')}
          className="rounded-lg bg-amber-500 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-600"
        >
          {t('Add')}
        </button>
        <button
          type="button"
          onClick={() => setState('dismissed')}
          className="rounded-lg border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50"
        >
          {t('No thanks')}
        </button>
      </div>
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
