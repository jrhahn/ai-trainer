import { useQuery } from '@tanstack/react-query'
import { Target } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { fetchAthletePredictions } from '../services/user'
import { ATHLETE_PREDICTIONS_QUERY_KEY } from './AthleteTraitsSettings'

/**
 * The coach's hit rate, with the misses one click away (ai-trainer-ops#16).
 *
 * The misses are the point: a hit rate nobody can check is a claim, and the
 * wrong calls are what make the right ones believable. So they are listed with
 * what was expected and what happened instead, not summarised away.
 *
 * Deliberately no confidence figure. Whether `confidence` is a frequency
 * ("0.6 = right 60% of the time") is still undecided (ai-trainer-ops#39 Q4),
 * and a hit rate means the same under either answer.
 *
 * Shares the predictions query key prefix with Settings, so marking a
 * prediction right or wrong there refreshes this card too.
 */
export default function PredictionScoreboard() {
  const authToken = useAppStore((s) => s.authToken)
  const { data } = useQuery({
    queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY, authToken, 'resolved'],
    queryFn: () => fetchAthletePredictions(authToken!, { includeResolved: true }),
    enabled: !!authToken,
  })

  const accuracy = data?.accuracy
  // Nothing scored yet: a "0 of 0" card would only say the feature exists.
  if (!data || !accuracy || accuracy.evaluated === 0) return null

  const misses = data.predictions.filter((p) => p.status === 'incorrect')
  const percent = Math.round((accuracy.correct / accuracy.evaluated) * 100)

  return (
    <section
      aria-labelledby="prediction-scoreboard-heading"
      className="bg-white rounded-2xl shadow-xs border border-gray-100 p-5"
    >
      <h2
        id="prediction-scoreboard-heading"
        className="flex items-center gap-1.5 text-base font-bold text-gray-900 mb-1"
      >
        <Target size={16} /> Your coach's track record
      </h2>
      <p className="text-sm text-gray-700">
        Your coach made {accuracy.evaluated} checkable{' '}
        {accuracy.evaluated === 1 ? 'prediction' : 'predictions'} about you.{' '}
        <strong>
          {accuracy.correct} came true ({percent}%)
        </strong>
        {misses.length > 0 ? '.' : ', none missed so far.'}
      </p>

      {misses.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm font-semibold text-amber-700">
            The {misses.length} it got wrong
          </summary>
          <ul className="mt-2 space-y-2">
            {misses.map((miss) => (
              <li key={miss.id} className="border border-gray-100 rounded-lg p-3 text-sm">
                <p className="text-gray-900">{miss.prediction}</p>
                <p className="text-xs text-gray-500 mt-1">Expected: {miss.expectedOutcome}</p>
                <p className="text-xs text-gray-700 mt-0.5">
                  What happened instead: {miss.actualOutcome || 'not recorded'}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
