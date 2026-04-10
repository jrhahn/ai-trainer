import { useNavigate } from 'react-router-dom'
import { CheckCircle, Clock, Zap, Heart } from 'lucide-react'
import type { TrainingDay } from '../store/useAppStore'

const typeColors: Record<TrainingDay['workoutType'], string> = {
  rest: 'bg-gray-200 text-gray-600',
  endurance: 'bg-blue-100 text-blue-700',
  intervals: 'bg-red-100 text-red-700',
  tempo: 'bg-orange-100 text-orange-700',
  race: 'bg-purple-100 text-purple-700',
  recovery: 'bg-green-100 text-green-700',
  strength: 'bg-teal-100 text-teal-700',
}

export default function WorkoutCard({ day, showDate }: { day: TrainingDay; showDate?: boolean }) {
  const navigate = useNavigate()

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
          {new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, {
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
          <Clock size={12} /> {day.durationMinutes} min
        </span>
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
