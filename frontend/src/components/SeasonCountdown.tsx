import { Flag } from 'lucide-react'
import { differenceInCalendarDays } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'

/** How far out the target event is.
 *
 * The app knew the date and the name of the event all along and showed
 * neither, so nothing on screen connected today's session to the reason for
 * doing it.
 */
export default function SeasonCountdown() {
  const { raceDate, raceDescription } = useAppStore(
    useShallow((s) => ({
      raceDate: s.userProfile?.raceDate,
      raceDescription: s.userProfile?.raceDescription,
    }))
  )

  if (!raceDate) return null

  const days = differenceInCalendarDays(parseLocalDate(raceDate), new Date())
  if (days < 0) return null

  // The field is free text ("80km gran fondo with 2000m climbing"), so take
  // whatever precedes a spaced dash as the event's name and leave the rest for
  // the event page.  The dash must be spaced: a bare hyphen belongs to the word
  // it sits in, and splitting on it turns "Time-trial championship" into "Time".
  const name = raceDescription?.split(/\s+[—–-]\s+/)[0]?.trim() || 'your target event'
  const dateLabel = parseLocalDate(raceDate).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  })

  return (
    <section className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
        <Flag size={16} aria-hidden="true" />
      </span>
      <p className="text-sm text-gray-900">
        <span className="font-bold">
          {days === 0 ? 'Race day' : days === 1 ? '1 day' : `${days} days`}
        </span>
        {days > 0 && <span className="text-gray-500"> to </span>}
        <span className="font-semibold">{name}</span>
      </p>
      <span className="ml-auto text-xs font-medium text-gray-400">{dateLabel}</span>
    </section>
  )
}
