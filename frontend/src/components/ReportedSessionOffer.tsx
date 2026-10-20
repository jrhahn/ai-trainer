import { useState } from 'react'
import { ClipboardCheck } from 'lucide-react'
import LogSessionDialog from './LogSessionDialog'
import { useLogPlannedSession } from '../hooks/useLogPlannedSession'
import type { ReportedSession } from '../utils/reportedSession'
import { useT } from '../i18n'

const SPORT_LABEL: Record<ReportedSession['sport'], string> = {
  cycling: 'Ride',
  running: 'Run',
  strength: 'Strength',
}

/** The form, mounted only once asked for: logging a planned session needs the
 *  query client, which the chat alone does not. */
function OfferedForm({
  session,
  onSaved,
  onClose,
}: {
  session: ReportedSession
  onSaved: () => void
  onClose: () => void
}) {
  const { logSession } = useLogPlannedSession()
  return (
    <LogSessionDialog open prefill={session} onLogPlanned={logSession} onSaved={onSaved} onClose={onClose} />
  )
}

/**
 * Under a reply to "I did that, 30 km, 1 h, 200 W NP": shall it be entered?
 * (ai-trainer-ops#166). Yes opens the one logging form (#171) prefilled with
 * what the athlete wrote; it asks the rest, including whether this was the
 * planned session, and nothing is saved until they confirm there.
 */
export default function ReportedSessionOffer({ session }: { session: ReportedSession }) {
  const t = useT()
  const [state, setState] = useState<'asking' | 'open' | 'saved' | 'dismissed'>('asking')

  if (state === 'dismissed') return null
  if (state === 'saved') {
    return <p className="mt-2 text-xs font-medium text-green-700">{t('Entered.')}</p>
  }

  const parts = [
    t(SPORT_LABEL[session.sport]),
    session.durationMinutes != null ? t('{minutes} min', { minutes: session.durationMinutes }) : null,
    session.distanceKm != null ? t('{km} km', { km: session.distanceKm }) : null,
    session.normalizedPower != null ? t('{watts} W NP', { watts: session.normalizedPower }) : null,
    session.averageHeartRate != null ? t('{bpm} bpm', { bpm: session.averageHeartRate }) : null,
  ].filter(Boolean)

  return (
    <div className="mt-2 border-t border-gray-200 pt-2">
      <p className="flex items-center gap-1 text-xs font-semibold text-gray-600">
        <ClipboardCheck size={12} aria-hidden="true" />
        {t('Shall I enter this as a session?')}
      </p>
      <p className="mt-0.5 text-xs text-gray-500">{parts.join(' · ')}</p>
      <div className="mt-1.5 flex gap-2">
        <button
          type="button"
          onClick={() => setState('open')}
          className="rounded-lg bg-amber-500 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-600"
        >
          {t('Enter it')}
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
        <OfferedForm
          session={session}
          onSaved={() => setState('saved')}
          onClose={() => setState((now) => (now === 'saved' ? now : 'asking'))}
        />
      )}
    </div>
  )
}
