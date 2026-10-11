import { useQuery } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import { fetchAthletePredictions } from '../services/user'
import { ATHLETE_PREDICTIONS_QUERY_KEY } from '../components/AthleteTraitsSettings'

/**
 * The athlete's predictions with their results, and whether any is scored.
 *
 * One query for the scoreboard and for the dashboard that decides whether its
 * "More" has anything in it (ai-trainer-ops#68): react-query shares the key, so
 * asking twice costs one request.
 */
export function useScoredPredictions() {
  const authToken = useAppStore((s) => s.authToken)
  const { data } = useQuery({
    queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY, authToken, 'resolved'],
    queryFn: () => fetchAthletePredictions(authToken!, { includeResolved: true }),
    enabled: !!authToken,
  })
  return { data, hasScored: (data?.accuracy?.evaluated ?? 0) > 0 }
}
