import { useEffect, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint, WorkoutFeedback } from '../store/useAppStore'
import { enteredActivitySlot } from '../utils/enteredActivity'
import { sportTypeText } from '../utils/activityType'
import { effortLabel } from '../utils/effort'
import { fetchWorkoutLogs } from '../services/user'
import { useEnteredActivityActions } from '../hooks/useEnteredActivityActions'
import LogSessionDialog, { type EditedActivity } from './LogSessionDialog'
import { useLanguage, useT, type Language } from '../i18n'
import { card, cardTitle, dotted, iconButton, iconDeleteButton } from './ui'

/**
 * What the athlete actually did on a day, whatever the plan said
 * (ai-trainer-ops#47, #48).
 *
 * The day view used to show the plan's sessions only. A ride on a rest day, a
 * run entered with "Add activity", an uploaded file on a day the plan never
 * covered — none of them appeared on the day they happened, and the upload
 * result had nowhere to point. Read from the same list the dashboard's recent
 * activities use, so the two never disagree about what a day holds.
 *
 * An activity the athlete entered shows what they entered, and can be changed
 * or deleted here with the dashboard's form and calls (ai-trainer-ops#207).
 * That includes days before the plan began: entering past sessions by hand is
 * one way to give the coach a history.
 */

function durationLabel(seconds: number | undefined, language: Language): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  // "1 h 30 min" in German, "1h 30m" in English (ai-trainer-ops#121).
  if (h > 0) return language === 'de' ? `${h} h ${m} min` : `${h}h ${m}m`
  return `${m} min`
}

function identity(ride: RideMetricPoint): string {
  return ride.externalActivityId ? `external:${ride.externalActivityId}` : `strava:${ride.stravaActivityId}`
}

/** What the athlete entered, each where they said it. */
function EnteredDetails({ log }: { log: WorkoutFeedback }) {
  const t = useT()
  const workoutType = log.sport === 'strength' ? 'strength' : 'endurance'
  const facts = [
    t('Effort: {level}/5 ({label})', {
      level: log.perceivedEffort,
      label: t(effortLabel({ workoutType }, log.perceivedEffort)),
    }),
    log.distanceKm != null && t('Distance: {km} km', { km: log.distanceKm }),
    log.averageHeartRate != null && t('Avg HR: {bpm} bpm', { bpm: log.averageHeartRate }),
    log.normalizedPower != null && t('NP: {watts} W', { watts: log.normalizedPower }),
  ].filter((fact): fact is string => Boolean(fact))
  return (
    <div className="text-gray-500">
      <ul>
        {facts.map((fact) => (
          <li key={fact} className={`inline ${dotted}`}>
            {fact}
          </li>
        ))}
      </ul>
      {log.notes && <p className="mt-1 whitespace-pre-line text-gray-500">{log.notes}</p>}
    </div>
  )
}

export default function DayActivities({ date }: { date: string }) {
  const rides = useAppStore((s) => s.rideMetricsHistory)
  const authToken = useAppStore((s) => s.authToken)
  const t = useT()
  const language = useLanguage()
  const actions = useEnteredActivityActions()
  const [logs, setLogs] = useState<Record<string, WorkoutFeedback>>({})
  const [editing, setEditing] = useState<EditedActivity | null>(null)

  const seen = new Set<string>()
  const onDay = rides.filter((ride) => {
    if (ride.activityDate !== date) return false
    const key = identity(ride)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  const entered = onDay.some((ride) => enteredActivitySlot(ride) != null)

  // The entered values live on the logs, not on the load rows. Read again
  // whenever the rides change, which is what a save or a delete does.
  useEffect(() => {
    if (!authToken || !entered) return
    let current = true
    fetchWorkoutLogs(authToken)
      .then((next) => {
        if (current) setLogs(next)
      })
      .catch(() => {
        // The activity still shows with its sport and duration.
      })
    return () => {
      current = false
    }
  }, [authToken, entered, rides])

  if (onDay.length === 0) return null

  return (
    <section className={card}>
      <h2 className={cardTitle}>{t('What you did this day')}</h2>
      <ul className="divide-y divide-gray-100">
        {onDay.map((ride) => {
          const slot = enteredActivitySlot(ride)
          const log = slot != null ? logs[`${ride.activityDate}#${slot}`] : undefined
          const sport = t(sportTypeText(ride.sportType))
          return (
            <li key={identity(ride)} className="py-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-gray-900">
                  <span className={`font-medium ${dotted}`}>{sport}</span>
                  <span className={dotted}>{slot != null ? t('Entered by you') : (ride.activityName ?? t('Activity'))}</span>
                </span>
                {ride.durationSeconds ? (
                  <span className="shrink-0 text-gray-500">{durationLabel(ride.durationSeconds, language)}</span>
                ) : null}
                {slot != null && (
                  <>
                    <button
                      type="button"
                      onClick={() => void actions.open(ride).then((edited) => edited && setEditing(edited))}
                      aria-label={t('Edit the {sport} you entered on {date}', { sport, date: ride.activityDate })}
                      className={iconButton}
                    >
                      <Pencil size={13} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void actions.remove(ride)}
                      aria-label={t('Delete the {sport} you entered on {date}', { sport, date: ride.activityDate })}
                      className={iconDeleteButton}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  </>
                )}
              </div>
              {log && <EnteredDetails log={log} />}
            </li>
          )
        })}
      </ul>
      {editing && (
        <LogSessionDialog
          key={`${editing.date}#${editing.slot}`}
          open
          editing={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  )
}
