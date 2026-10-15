import { Target } from 'lucide-react'
import { useScoredPredictions } from '../hooks/useScoredPredictions'
import { useT } from '../i18n'

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
  const { data } = useScoredPredictions()
  const t = useT()

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
        <Target size={16} /> {t("Your coach's track record")}
      </h2>
      <p className="text-sm text-gray-700">
        {t(
          accuracy.evaluated === 1
            ? 'Your coach made {n} checkable prediction about you.'
            : 'Your coach made {n} checkable predictions about you.',
          { n: accuracy.evaluated },
        )}{' '}
        <strong>{t('{correct} came true ({percent}%)', { correct: accuracy.correct, percent })}</strong>
        {misses.length > 0 ? '.' : t(', none missed so far.')}
      </p>

      {misses.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm font-semibold text-amber-700">
            {t('The {n} it got wrong', { n: misses.length })}
          </summary>
          <ul className="mt-2 space-y-2">
            {misses.map((miss) => (
              <li key={miss.id} className="border border-gray-100 rounded-lg p-3 text-sm">
                <p className="text-gray-900">{miss.prediction}</p>
                <p className="text-xs text-gray-500 mt-1">{t('Expected: {outcome}', { outcome: miss.expectedOutcome })}</p>
                <p className="text-xs text-gray-700 mt-0.5">
                  {t('What happened instead: {outcome}', { outcome: miss.actualOutcome || t('not recorded') })}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
