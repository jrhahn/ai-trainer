import { useNavigate } from 'react-router-dom'
import { CheckCircle, Clock, Zap, Heart } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'
import { parseLocalDate } from '../utils/workout'
import { formatPlanDuration } from '../utils/planDuration'
import WeatherBadge from './WeatherBadge'

const typeColors: Record<TrainingDay['workoutType'], string> = {
  rest: 'bg-gray-100 text-gray-600',
  endurance: 'bg-gray-100 text-gray-700',
  intervals: 'bg-gray-900 text-white',
  tempo: 'bg-gray-800 text-white',
  race: 'bg-gray-900 text-white',
  recovery: 'bg-gray-100 text-gray-600',
  strength: 'bg-gray-200 text-gray-700',
}

export default function WorkoutCard({
  day,
  showDate,
  compact,
}: {
  day: TrainingDay
  showDate?: boolean
  compact?: boolean
}) {
  const navigate = useNavigate()
  // Forecast for this planned day, when it falls inside the horizon (#495).
  const forecast = useAppStore(useShallow((s) => s.weatherForecast[day.date]))

  if (compact) {
    return (
      <div
        onClick={() => navigate(`/workout/${day.date}`)}
        className="flex items-center gap-2 bg-white rounded-lg border border-gray-100 px-3 py-2 cursor-pointer hover:border-gray-300 transition-colors"
      >
        <p className="text-xs text-gray-400 flex-shrink-0 w-16">
          {parseLocalDate(day.date).toLocaleDateString(undefined, {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
          })}
        </p>
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded-full capitalize flex-shrink-0 ${
            typeColors[day.workoutType]
          }`}
        >
          {day.workoutType}
        </span>
        <span className="text-xs text-gray-700 font-medium flex-1 truncate">{day.title}</span>
        <WeatherBadge forecast={forecast} size={11} className="flex-shrink-0 text-xs" />
        <span className="flex items-center gap-1 text-xs text-gray-400 flex-shrink-0">
          <Clock size={11} />
          {formatPlanDuration(day)}
        </span>
        {day.completed && <CheckCircle size={14} className="text-green-500 flex-shrink-0" />}
      </div>
    )
  }

  return (
    <div
      onClick={() => navigate(`/workout/${day.date}`)}
      className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 cursor-pointer hover:shadow-md transition-shadow relative"
    >
      {day.completed && (
        <span className="absolute top-3 right-3 text-green-500">
          <CheckCircle size={18} />
        </span>
      )}
      {showDate && (
        <p className="text-xs text-gray-400 mb-1">
          {parseLocalDate(day.date).toLocaleDateString(undefined, {
            weekday: 'short',
            month: 'short',
            day: 'numeric',
          })}
        </p>
      )}
      <span
        className={`inline-block text-xs font-semibold px-2 py-0.5 rounded-full mb-2 capitalize ${
          typeColors[day.workoutType]
        }`}
      >
        {day.workoutType}
      </span>
      <h3 className="font-semibold text-gray-900 text-sm leading-snug">{day.title}</h3>
      <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-1">
          <Clock size={12} /> {formatPlanDuration(day)}
        </span>
        <WeatherBadge forecast={forecast} />

        {day.targetPower && (
          <span className="flex items-center gap-1">
            <Zap size={12} /> {day.targetPower.low}-{day.targetPower.high}W
          </span>
        )}
        {day.targetHeartRate && (
          <span className="flex items-center gap-1">
            <Heart size={12} /> {day.targetHeartRate.low}-{day.targetHeartRate.high} bpm
          </span>
        )}
      </div>
    </div>
  )
}
