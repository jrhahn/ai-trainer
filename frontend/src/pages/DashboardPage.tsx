import { useEffect, useRef } from 'react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import WorkoutCard from '../components/WorkoutCard'
import AIChat from '../components/AIChat'
import { useStravaSync } from '../hooks/useStravaSync'
import { adaptTrainingPlan } from '../services/ai'

export default function DashboardPage() {
  const { userProfile, trainingPlan, authToken, setTrainingPlan } = useAppStore(
    useShallow((s) => ({
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      authToken: s.authToken,
      setTrainingPlan: s.setTrainingPlan,
    }))
  )

  const adaptationTriggeredRef = useRef(false)

  // keep sync running so analysis status updates remain active
  useStravaSync()

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const today = new Date().toISOString().split('T')[0]

  // Always show the next 3 upcoming days (today or later)
  const next3Days = trainingPlan.filter((d) => d.date >= today).slice(0, 3)

  // If there are past incomplete days the plan is stale — ask the AI coach to
  // reschedule them so the athlete always has current upcoming sessions.
  const hasStalePlan =
    trainingPlan.length > 0 && trainingPlan.some((d) => d.date < today && !d.completed)

  useEffect(() => {
    if (!hasStalePlan) {
      // Reset so a future stale state can trigger adaptation again
      adaptationTriggeredRef.current = false
      return
    }
    if (!authToken || adaptationTriggeredRef.current) return
    adaptationTriggeredRef.current = true
    adaptTrainingPlan([], authToken)
      .then((updatedPlan) => setTrainingPlan(updatedPlan))
      .catch(() => {
        // keep existing plan on error; ref stays true to avoid infinite retries
      })
  }, [hasStalePlan, authToken, setTrainingPlan])

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {greeting()}, {userProfile?.name?.split(' ')[0] ?? 'Athlete'}! 👋
        </h1>
        <p className="text-gray-500 text-sm mt-0.5">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
      </div>

      {/* Next 3 days */}
      {next3Days.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Next 3 days</h2>
          <div className="space-y-1.5">
            {next3Days.map((day) => (
              <WorkoutCard key={day.date} day={day} compact />
            ))}
          </div>
        </div>
      )}

      {/* Ask your coach — takes up the majority of the remaining space */}
      <div className="flex flex-col">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Ask your coach</h2>
        <AIChat
          contextWorkout={trainingPlan.find((d) => d.date === today)}
          className="flex-1 h-[calc(100vh-22rem)] min-h-[24rem] shadow-sm"
        />
      </div>
    </div>
  )
}
