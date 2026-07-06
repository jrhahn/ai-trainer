import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { TrainingDay } from '../store/useAppStore'
import { formatPlanDuration } from '../utils/planDuration'

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

export default function TodayCard({ today, trainingPlan }: Props) {
  const todayWorkout = trainingPlan.find((d) => d.date === today)

  if (!todayWorkout || todayWorkout.workoutType === 'rest') {
    return (
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
        <div className="flex items-center gap-2 mb-1">
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
            Rest day
          </span>
          {todayWorkout?.completed && (
            <span className="text-xs text-green-600 font-medium">✓ Done</span>
          )}
        </div>
        <p className="text-lg font-semibold text-gray-800 mt-1">Recovery &amp; rest</p>
        {!todayWorkout && (
          <p className="text-sm text-gray-400 mt-1">No workout scheduled for today.</p>
        )}
      </div>
    )
  }

  const colors = WORKOUT_COLORS[todayWorkout.workoutType] ?? 'bg-gray-100 text-gray-600'

  return (
    <Link to={`/workout/${today}`} className="block group">
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 hover:border-amber-200 hover:shadow-md transition-all">
        <div className="flex items-center gap-2 mb-1">
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium capitalize ${colors}`}>
            {todayWorkout.workoutType}
          </span>
          {todayWorkout.completed && (
            <span className="text-xs text-green-600 font-medium">✓ Done</span>
          )}
        </div>
        <p className="text-xl font-bold text-gray-900 mt-1 group-hover:text-amber-600 transition-colors">
          {todayWorkout.title}
        </p>
        <div className="flex flex-wrap items-center gap-3 mt-2 text-sm text-gray-500">
          {todayWorkout.durationMinutes > 0 && (
            <span>{formatPlanDuration(todayWorkout)}</span>
          )}
          {todayWorkout.targetPower && (
            <span>{todayWorkout.targetPower.low}–{todayWorkout.targetPower.high} W</span>
          )}
          {todayWorkout.targetHeartRate && (
            <span>{todayWorkout.targetHeartRate.low}–{todayWorkout.targetHeartRate.high} bpm</span>
          )}
        </div>
        {todayWorkout.description && (
          <p className="text-sm text-gray-500 mt-2 line-clamp-2">{todayWorkout.description}</p>
        )}
        <div className="flex items-center gap-1 mt-3 text-xs font-semibold text-amber-600">
          {todayWorkout.completed ? 'View details' : 'View & log workout'}
          <ArrowRight size={13} className="group-hover:translate-x-0.5 transition-transform" />
        </div>
      </div>
    </Link>
  )
}
