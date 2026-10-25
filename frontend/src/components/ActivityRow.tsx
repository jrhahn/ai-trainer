import { BatteryFull, BatteryLow, BatteryMedium, Pencil, Trash2 } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { dotted, iconButton, iconDeleteButton } from './ui'
import AmbiguousMatchResolver from './AmbiguousMatchResolver'
import SessionPurposeQuestion from './SessionPurposeQuestion'
import { WeatherIcon } from './WeatherBadge'
import { setRideLegs } from '../services/user'
import { sportTypeText } from '../utils/activityType'
import { enteredActivitySlot } from '../utils/enteredActivity'
import { formatPlanDuration } from '../utils/planDuration'
import { buildMatchCoachPrompt, planForRide, rideMatchBadge } from '../utils/rideMatch'
import { formatTemperature } from '../utils/weather'
import { formatDuration, parseLocalDate } from '../utils/workout'

/** Quick leg-freshness ratings tappable directly on each activity card.
 *
 * A battery rather than a traffic light (ai-trainer-ops#51.7). The three
 * circles were 🟢🟡🔴, which carried the whole meaning in colour — nothing at
 * all to a red-green colourblind athlete, and three empty boxes in a browser
 * with no emoji font. A battery says the same thing by how full it is. The
 * three are grey, and the chosen one takes the accent (owner's review,
 * 2026-10-24: one accent, no green, amber and red side by side).
 */
const LEG_FEELINGS = [
  { value: 'fresh', Icon: BatteryFull, label: 'Fresh' },
  { value: 'normal', Icon: BatteryMedium, label: 'Normal' },
  { value: 'heavy', Icon: BatteryLow, label: 'Heavy' },
] as const

// One activity row: the ride, what was planned, the match, the legs, and the
// two questions only the athlete can answer (#574, #580). One text size and
// grey throughout; the chosen legs rating is the only accent (owner's review,
// 2026-10-24).
//
// Out of `DashboardPage` (ai-trainer-ops#274).
export default function ActivityRow({
  ride,
  isNew,
  onEdit,
  onRemove,
}: {
  ride: RideMetricPoint
  isNew: boolean
  onEdit: (ride: RideMetricPoint) => void
  onRemove: (ride: RideMetricPoint) => void
}) {
  const t = useT()
  const planText = usePlanText()
  const language = useLanguage()
  const { trainingPlan, authToken, updateRideMetricLegs, setPendingCoachMessage } = useAppStore(
    useShallow((s) => ({
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      updateRideMetricLegs: s.updateRideMetricLegs,
      setPendingCoachMessage: s.setPendingCoachMessage,
    }))
  )

  // Quick "how the legs felt" tap on an activity card. Optimistically update the
  // store, persist to the backend, and revert on failure. Tapping the active
  // rating again clears it (back to unset, which is ignored everywhere).
  const handleSetLegs = (ride: RideMetricPoint, legs: 'fresh' | 'normal' | 'heavy') => {
    if (!authToken) return
    const next = ride.feelLegs === legs ? null : legs
    const previous = ride.feelLegs ?? null
    updateRideMetricLegs(ride.stravaActivityId, next)
    void setRideLegs(authToken, ride.stravaActivityId, next, ride.externalActivityId).catch(() => {
      updateRideMetricLegs(ride.stravaActivityId, previous)
    })
  }

  const plan = planForRide(ride, trainingPlan)
  const { score, label: scoreLabel } = rideMatchBadge(ride, plan)
  const sport = t(sportTypeText(ride.sportType))
  return (
    <div className="py-2 text-sm">
      {/* Activity row */}
      <div className="flex items-center gap-2">
        <p className="w-24 shrink-0 text-gray-500">
          {parseLocalDate(ride.activityDate).toLocaleDateString(localeTag(language), {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
          })}
        </p>
        <span className="min-w-0 flex-1 truncate text-gray-900">
          <span className={`font-medium ${dotted}`}>{sport}</span>
          <span className={dotted}>
            {enteredActivitySlot(ride) != null ? t('Entered by you') : (ride.activityName ?? t('Activity'))}
          </span>
          {isNew && <span className={`text-gray-500 ${dotted}`}>{t('new')}</span>}
        </span>
        {ride.weatherTemperatureC != null && (
          <span
            className="flex shrink-0 items-center gap-1 text-gray-500"
            title={t(ride.weatherCondition?.replace(/_/g, ' ') ?? 'Weather')}
          >
            <WeatherIcon condition={ride.weatherCondition} />
            {formatTemperature(ride.weatherTemperatureC)}
          </span>
        )}
        {ride.durationSeconds != null && (
          <span className="shrink-0 text-gray-500">{formatDuration(ride.durationSeconds, language)}</span>
        )}
        {enteredActivitySlot(ride) != null && (
          <button
            type="button"
            onClick={() => void onEdit(ride)}
            aria-label={t('Edit the {sport} you entered on {date}', { sport, date: ride.activityDate })}
            className={iconButton}
          >
            <Pencil size={13} aria-hidden="true" />
          </button>
        )}
        {enteredActivitySlot(ride) != null && (
          <button
            type="button"
            onClick={() => void onRemove(ride)}
            aria-label={t('Delete the {sport} you entered on {date}', { sport, date: ride.activityDate })}
            className={iconDeleteButton}
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
        )}
      </div>
      {/* Plan comparison row */}
      <div className="flex items-center gap-2 sm:ml-26">
        <span className="shrink-0 text-gray-500">{t('planned:')}</span>
        <span className="min-w-0 flex-1 truncate text-gray-500">
          {plan ? (
            <>
              {planText(plan.title) ?? plan.workoutType}
              {plan.durationMinutes ? ` · ${formatPlanDuration(plan, language)}` : ''}
              {plan.targetPower ? ` · ${plan.targetPower.low}–${plan.targetPower.high}W` : ''}
            </>
          ) : (
            t('No planned workout found')
          )}
        </span>
        {plan && (
          <button
            onClick={() =>
              setPendingCoachMessage(buildMatchCoachPrompt(ride, plan, score, t))
            }
            title={t('Ask coach about this match')}
            className="shrink-0 font-medium text-gray-700 hover:text-gray-900"
          >
            {t(scoreLabel)}
          </button>
        )}
        {authToken && (
          <div
            className="flex shrink-0 items-center"
            role="group"
            aria-label={t('How did your legs feel?')}
          >
            {LEG_FEELINGS.map((feel) => {
              const active = ride.feelLegs === feel.value
              return (
                <button
                  key={feel.value}
                  type="button"
                  onClick={() => handleSetLegs(ride, feel.value)}
                  title={t('Legs: {feel}', { feel: t(feel.label) })}
                  aria-label={t('Legs felt {feel}', { feel: t(feel.label) })}
                  aria-pressed={active}
                  className={
                    active
                      ? 'inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700'
                      : iconButton
                  }
                >
                  {/* `aria-hidden`: the button is already labelled "Legs felt Fresh". */}
                  <feel.Icon size={14} aria-hidden="true" />
                </button>
              )
            })}
          </div>
        )}
      </div>
      {/* The matcher refused to guess which session this was; this is
          where the athlete answers it (#574). Renders nothing for the
          normal, unambiguous ride. */}
      <AmbiguousMatchResolver ride={ride} />
      {/* And the other question the coach cannot answer on its own:
          what this session was, when the data could not say (#580).
          Also renders nothing for a ride that classified cleanly. */}
      <SessionPurposeQuestion ride={ride} />
    </div>
  )
}
