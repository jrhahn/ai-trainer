import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { DailyForecast, TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'
import { sessionKey, sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import WeatherBadge from './WeatherBadge'

const WORKOUT_COLORS: Record<string, string> = {
  rest: 'bg-gray-100 text-gray-500',
  recovery: 'bg-green-100 text-green-700',
  endurance: 'bg-blue-100 text-blue-700',
  tempo: 'bg-orange-100 text-orange-700',
  intervals: 'bg-red-100 text-red-700',
  race: 'bg-purple-100 text-purple-700',
  strength: 'bg-yellow-100 text-yellow-700',
}

interface Props {
  today: string
  trainingPlan: TrainingDay[]
}

/** The workout link for a session — the slot is only needed for a two-a-day. */
function workoutHref(day: TrainingDay): string {
  const slot = sessionSlot(day)
  return slot > 0 ? `/workout/${day.date}?slot=${slot}` : `/workout/${day.date}`
}

function SessionCard({
  day,
  sessionCount,
  forecast,
}: {
  day: TrainingDay
  sessionCount: number
  forecast: DailyForecast | undefined
}) {
  const colors = WORKOUT_COLORS[day.workoutType] ?? 'bg-gray-100 text-gray-600'
  const label = sessionLabel(day, sessionCount)

  return (
    <Link to={workoutHref(day)} className="block group">
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 hover:border-amber-200 hover:shadow-md transition-all">
        <div className="flex items-center gap-2 mb-1">
          {label && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold uppercase bg-gray-900 text-white">
              {label}
            </span>
          )}
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium capitalize ${colors}`}>
            {day.workoutType}
          </span>
          {day.completed && (
            <span className="text-xs text-green-600 font-medium">✓ Done</span>
          )}
          <WeatherBadge forecast={forecast} className="ml-auto text-xs" />
        </div>
        <p className="text-xl font-bold text-gray-900 mt-1 group-hover:text-amber-600 transition-colors">
          {day.title}
        </p>
        <div className="flex flex-wrap items-center gap-3 mt-2 text-sm text-gray-500">
          {day.durationMinutes > 0 && <span>{formatPlanDuration(day)}</span>}
          {day.targetPower && (
            <span>{day.targetPower.low}–{day.targetPower.high} W</span>
          )}
          {day.targetHeartRate && (
            <span>{day.targetHeartRate.low}–{day.targetHeartRate.high} bpm</span>
          )}
        </div>
        {day.description && (
          <p className="text-sm text-gray-500 mt-2 line-clamp-2">{day.description}</p>
        )}
        <div className="flex items-center gap-1 mt-3 text-xs font-semibold text-amber-600">
          {day.completed ? 'View details' : 'View & log workout'}
          <ArrowRight size={13} className="group-hover:translate-x-0.5 transition-transform" />
        </div>
      </div>
    </Link>
  )
}

export default function TodayCard({ today, trainingPlan }: Props) {
  // Every session planned for today, in AM→PM order. A date can hold a
  // two-a-day, and the old `.find()` silently rendered only the first (#496).
  const todaySessions = sessionsForDate(trainingPlan, today)
  // Today's forecast near the athlete's training location (#495) — shown on the
  // planned session so the conditions are visible before they head out.
  const forecast = useAppStore(useShallow((s) => s.weatherForecast[today]))

  const trainingSessions = todaySessions.filter((d) => d.workoutType !== 'rest')

  // No session, or every session on the date is a rest day.
  if (trainingSessions.length === 0) {
    const restDay = todaySessions[0]
    return (
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
        <div className="flex items-center gap-2 mb-1">
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
            Rest day
          </span>
          {restDay?.completed && (
            <span className="text-xs text-green-600 font-medium">✓ Done</span>
          )}
          <WeatherBadge forecast={forecast} className="ml-auto text-xs" />
        </div>
        <p className="text-lg font-semibold text-gray-800 mt-1">Recovery &amp; rest</p>
        {!restDay && (
          <p className="text-sm text-gray-400 mt-1">No workout scheduled for today.</p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {trainingSessions.map((day) => (
        <SessionCard
          key={sessionKey(day)}
          day={day}
          // A label ("AM" / "1/2") only appears once the day really holds more
          // than one session, so a normal single-workout day looks as before.
          sessionCount={trainingSessions.length}
          forecast={forecast}
        />
      ))}
    </div>
  )
}
