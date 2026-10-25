import { differenceInCalendarDays } from 'date-fns'
import { useProfile } from '../queries/profile'
import { parseLocalDate } from '../utils/workout'
import { localeTag, useLanguage, useT } from '../i18n'
import { card } from './ui'

/** How far out the target event is.
 *
 * The app knew the date and the name of the event all along and showed
 * neither, so nothing on screen connected today's session to the reason for
 * doing it.
 */
export default function SeasonCountdown() {
  const profile = useProfile()
  const raceDate = profile?.raceDate
  const raceDescription = profile?.raceDescription
  const t = useT()
  const language = useLanguage()

  if (!raceDate) return null

  const days = differenceInCalendarDays(parseLocalDate(raceDate), new Date())
  if (days < 0) return null

  // The field is free text ("80km gran fondo with 2000m climbing"), so take
  // whatever precedes a spaced dash as the event's name and leave the rest for
  // the event page.  The dash must be spaced: a bare hyphen belongs to the word
  // it sits in, and splitting on it turns "Time-trial championship" into "Time".
  const name = raceDescription?.split(/\s+[—–-]\s+/)[0]?.trim() || t('your target event')
  const dateLabel = parseLocalDate(raceDate).toLocaleDateString(localeTag(language), {
    day: 'numeric',
    month: 'short',
  })

  return (
    <section className={`${card} flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm`}>
      <p className="text-gray-900">
        <span className="font-semibold">
          {days === 0 ? t('Race day') : days === 1 ? t('1 day') : t('{days} days', { days })}
        </span>
        {days > 0 && <span className="text-gray-500"> {t('to')} </span>}
        <span className="font-semibold">{name}</span>
      </p>
      <span className="ml-auto text-gray-500">{dateLabel}</span>
    </section>
  )
}
