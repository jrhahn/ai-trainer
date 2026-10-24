import { useEffect, useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { card, cardTitle, textButton } from './ui'
import { useAppStore } from '../store/useAppStore'
import { fetchSeasonPlan, writeWeekGoals, type SeasonWeek } from '../services/ai'
import { parseLocalDate } from '../utils/workout'
import { localeTag, useLanguage, useT, type Translate } from '../i18n'

const PHASE_LABEL: Record<SeasonWeek['phase'], string> = {
  base: 'Base',
  build: 'Build',
  peak: 'Peak',
  taper: 'Taper',
  transition: 'Transition',
  maintenance: 'Maintenance',
}

function hardLabel(t: Translate, min: number, max: number): string {
  const sessions = (n: number) =>
    n === 1 ? t('1 hard session') : t('{count} hard sessions', { count: n })
  if (max === 0) return t('no hard sessions')
  if (min === max) return sessions(max)
  if (min === 0) return t('at most {sessions}', { sessions: sessions(max) })
  return t('{min}–{max} hard sessions', { min, max })
}

/** Rhythm, hours and hard sessions, the targets the days are checked against. */
function weekTargets(t: Translate, week: SeasonWeek): string {
  const rhythm = week.recovery
    ? t('Recovery week')
    : week.loadWeek
      ? t('Load week {n} of 3', { n: week.loadWeek })
      : null
  const hours =
    week.hoursMax == null
      ? null
      : week.hoursMin === week.hoursMax
        ? `${week.hoursMax} h`
        : `${week.hoursMin}–${week.hoursMax} h`
  return [rhythm, hours, hardLabel(t, week.hardSessionsMin, week.hardSessionsMax)].filter(Boolean).join(' · ')
}

/** The weeks ahead, coarse: phase, focus, targets and a goal each (ai-trainer-ops#98).
 *
 * The plan below is two weeks of days. This is what they are for: the week
 * they fall in and the ones after it, up to twelve. The numbers come from the
 * server's rules; only the goal sentence is the model's, and a week shows none
 * until it has been written. Read again when a race is added, because a race
 * is what moves the weeks.
 */
export default function SeasonPlan() {
  const t = useT()
  const language = useLanguage()
  const { authToken, raceEvents } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, raceEvents: s.raceEvents }))
  )
  const [weeks, setWeeks] = useState<SeasonWeek[]>([])
  // The weeks are the direction, not the plan: the page works without them.
  // A failed read still says so, so "no season plan" and "season plan broken"
  // look different (ai-trainer-ops#135).
  const [loadFailed, setLoadFailed] = useState(false)
  const [goals, setGoals] = useState<'idle' | 'writing' | 'failed'>('idle')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    const load = async () => {
      let current: SeasonWeek[]
      try {
        current = await fetchSeasonPlan(authToken)
      } catch (err) {
        console.error('[SeasonPlan] Failed to load the season plan:', err)
        if (cancelled) return
        // A goal write cancelled by this reload never reports back; its hint must not stay.
        setGoals('idle')
        setLoadFailed(true)
        return
      }
      if (cancelled) return
      setLoadFailed(false)
      setWeeks(current)
      if (!current.some((week) => !week.goal)) {
        setGoals('idle')
        return
      }
      setGoals('writing')
      try {
        const written = await writeWeekGoals(authToken)
        if (cancelled) return
        setWeeks(written)
        setGoals('idle')
      } catch (err) {
        // A missing goal is asked for again on the next visit.
        console.error('[SeasonPlan] Failed to write the week goals:', err)
        if (!cancelled) setGoals('failed')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authToken, raceEvents, attempt])

  if (weeks.length === 0) {
    if (!loadFailed) return null
    return (
      <section className={`${card} flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-500`}>
        <span>{t('The weeks ahead could not be loaded.')}</span>
        <button type="button" onClick={() => setAttempt((n) => n + 1)} className={textButton}>
          {t('Try again')}
        </button>
      </section>
    )
  }
  const [current, ...ahead] = weeks

  return (
    <section aria-labelledby="season-plan-title" className={`${card} text-sm text-gray-700`}>
      <div className="space-y-1">
        <h2 id="season-plan-title" className={cardTitle}>
          {t('This week')}: {t(PHASE_LABEL[current.phase])} · {t(current.focus)}
        </h2>
        <p className="text-gray-500">{weekTargets(t, current)}</p>
        {current.goal && <p>{current.goal}</p>}
        {goals === 'writing' && <p className="text-gray-500">{t('Writing the week goals…')}</p>}
        {goals === 'failed' && (
          <p className="text-gray-500">{t('The week goals follow on your next visit.')}</p>
        )}
        {loadFailed && (
          <p className="text-gray-500">
            {t('The weeks ahead could not be updated.')}{' '}
            <button type="button" onClick={() => setAttempt((n) => n + 1)} className={textButton}>
              {t('Try again')}
            </button>
          </p>
        )}
      </div>
      {ahead.length > 0 && (
        <details className="mt-3">
          <summary className={`cursor-pointer ${textButton}`}>
            {ahead.length === 1 ? t('The next week') : t('The next {count} weeks', { count: ahead.length })}
          </summary>
          <ol className="mt-2 space-y-2">
            {ahead.map((week) => (
              <li key={week.weekStart} className="border-t border-gray-100 pt-2">
                <p className="font-medium text-gray-900">
                  <span className="font-normal text-gray-500">
                    {parseLocalDate(week.weekStart).toLocaleDateString(localeTag(language), { day: 'numeric', month: 'short' })}
                  </span>{' '}
                  · {t(PHASE_LABEL[week.phase])} · {t(week.focus)}
                </p>
                <p className="text-gray-500">{weekTargets(t, week)}</p>
                {week.goal && <p>{week.goal}</p>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  )
}
