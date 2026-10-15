import { useEffect, useState } from 'react'
import { Milestone } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
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

  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    const load = async () => {
      try {
        const current = await fetchSeasonPlan(authToken)
        if (cancelled) return
        setWeeks(current)
        if (current.some((week) => !week.goal)) {
          const written = await writeWeekGoals(authToken)
          if (!cancelled) setWeeks(written)
        }
      } catch {
        // The weeks are the direction, not the plan: the page works without them,
        // and a missing goal is asked for again on the next visit.
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authToken, raceEvents])

  if (weeks.length === 0) return null
  const [current, ...ahead] = weeks

  return (
    <section
      aria-labelledby="season-plan-title"
      className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-gray-700"
    >
      <div className="flex gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
          <Milestone size={16} aria-hidden="true" />
        </span>
        <div className="min-w-0 space-y-1">
          <h2 id="season-plan-title" className="font-semibold text-gray-900">
            {t('This week')}: {t(PHASE_LABEL[current.phase])} · {current.focus}
          </h2>
          <p className="text-gray-500">{weekTargets(t, current)}</p>
          {current.goal && <p>{current.goal}</p>}
        </div>
      </div>
      {ahead.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-semibold text-gray-500 hover:text-gray-700">
            {ahead.length === 1 ? t('The next week') : t('The next {count} weeks', { count: ahead.length })}
          </summary>
          <ol className="mt-2 space-y-2">
            {ahead.map((week) => (
              <li key={week.weekStart} className="border-t border-slate-100 pt-2 text-xs">
                <p className="font-semibold text-gray-900">
                  <span className="text-gray-500">
                    {parseLocalDate(week.weekStart).toLocaleDateString(localeTag(language), { day: 'numeric', month: 'short' })}
                  </span>{' '}
                  · {t(PHASE_LABEL[week.phase])} · {week.focus}
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
