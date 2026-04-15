import { useNavigate } from 'react-router-dom'
import { CheckCircle } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import type { TrainingDay } from '../store/useAppStore'

const typeColors: Record<TrainingDay['workoutType'], string> = {
  rest: 'bg-gray-100 text-gray-400 border-gray-200',
  endurance: 'bg-blue-50 text-blue-700 border-blue-200',
  intervals: 'bg-red-50 text-red-700 border-red-200',
  tempo: 'bg-orange-50 text-orange-700 border-orange-200',
  race: 'bg-purple-50 text-purple-700 border-purple-200',
  recovery: 'bg-green-50 text-green-700 border-green-200',
  strength: 'bg-teal-50 text-teal-700 border-teal-200',
}

const typeEmoji: Record<TrainingDay['workoutType'], string> = {
  rest: '😴',
  endurance: '🚴',
  intervals: '⚡',
  tempo: '🔥',
  race: '🏆',
  recovery: '💚',
  strength: '💪',
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function TrainingCalendar() {
  const navigate = useNavigate()
  const plan = useAppStore((s) => s.trainingPlan)
  const today = new Date().toISOString().split('T')[0]

  const firstDate = plan.length > 0 ? new Date(plan[0].date + 'T12:00:00') : new Date()
  const dayOfWeek = firstDate.getDay()
  const monday = new Date(firstDate)
  monday.setDate(firstDate.getDate() - ((dayOfWeek + 6) % 7))

  const weeks: (TrainingDay | null)[][] = []
  for (let w = 0; w < 4; w++) {
    const week: (TrainingDay | null)[] = []
    for (let d = 0; d < 7; d++) {
      const date = new Date(monday)
      date.setDate(monday.getDate() + w * 7 + d)
      const iso = date.toISOString().split('T')[0]
      week.push(plan.find((p) => p.date === iso) ?? null)
    }
    weeks.push(week)
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      <div className="grid grid-cols-7 border-b">
        {DAYS.map((d) => (
          <div key={d} className="py-2 text-center text-xs font-semibold text-gray-400 border-r last:border-r-0">
            {d}
          </div>
        ))}
      </div>
      {weeks.map((week, wi) => (
        <div key={wi} className="grid grid-cols-7 border-b last:border-b-0">
          {week.map((day, di) => {
            if (!day) {
              return <div key={di} className="min-h-[90px] border-r last:border-r-0 bg-gray-50" />
            }
            const isToday = day.date === today
            const isPast = day.date < today
            return (
              <div
                key={di}
                onClick={() => navigate(`/workout/${day.date}`)}
                className={`min-h-[90px] border-r last:border-r-0 p-1.5 cursor-pointer hover:brightness-95 transition-all relative ${
                  typeColors[day.workoutType]
                } ${isToday ? 'ring-2 ring-inset ring-amber-500' : ''} ${
                  isPast && !day.completed ? 'opacity-60' : ''
                }`}
              >
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-xs font-bold">
                    {new Date(day.date + 'T12:00:00').getDate()}
                  </span>
                  {day.completed && (
                    <CheckCircle size={12} className="text-green-500 flex-shrink-0" />
                  )}
                </div>
                <div className="text-base leading-none mb-0.5">{typeEmoji[day.workoutType]}</div>
                <p className="text-xs font-medium leading-tight truncate">{day.title}</p>
                {day.workoutType !== 'rest' && (
                  <p className="text-xs opacity-70">{day.durationMinutes}m</p>
                )}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
