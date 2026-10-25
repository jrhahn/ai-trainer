import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import { useAuthToken } from '../hooks/useAuthToken'
import { button, deleteButton, textButton } from './ui'
import {
  clearAllMemory,
  exportMemory,
  fetchAthleteHypotheses,
  fetchAthleteMemoryFacts,
  fetchAthleteOpenQuestions,
  fetchAthletePredictions,
  fetchMemoryPrivacySettings,
  fetchValidationExperiments,
  updateMemoryPrivacySettings,
  type AthleteMemoryFact,
  type AthletePredictionAccuracy,
} from '../services/user'
import { slots, useT } from '../i18n'
import {
  ATHLETE_HYPOTHESES_QUERY_KEY,
  ATHLETE_OPEN_QUESTIONS_QUERY_KEY,
  ATHLETE_PREDICTIONS_QUERY_KEY,
  ATHLETE_TRAITS_QUERY_KEY,
  MEMORY_PRIVACY_QUERY_KEY,
  VALIDATION_EXPERIMENTS_QUERY_KEY,
} from '../queries/keys'
import { ExperimentRow, HypothesisRow, OpenQuestionRow, PredictionRow, TraitRow } from './athleteModel/rows'

// Five traits per list, then "Show all": a long memory pushed the rest of
// Settings far down (owner's review, 2026-10-23).
const TRAITS_SHOWN = 5

export default function AthleteTraitsSettings() {
  const authToken = useAuthToken()
  const queryClient = useQueryClient()
  const t = useT()

  const { data, isLoading, isError } = useQuery({
    queryKey: [ATHLETE_TRAITS_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteMemoryFacts(authToken),
    enabled: !!authToken,
  })

  const { data: hypotheses } = useQuery({
    queryKey: [ATHLETE_HYPOTHESES_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteHypotheses(authToken),
    enabled: !!authToken,
  })

  const { data: openQuestions } = useQuery({
    queryKey: [ATHLETE_OPEN_QUESTIONS_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteOpenQuestions(authToken),
    enabled: !!authToken,
  })

  const { data: experiments } = useQuery({
    queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY, authToken],
    queryFn: () => fetchValidationExperiments(authToken),
    enabled: !!authToken,
  })

  const { data: predictionsData } = useQuery({
    queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY, authToken],
    queryFn: () => fetchAthletePredictions(authToken),
    enabled: !!authToken,
  })
  const predictions = predictionsData?.predictions
  const predictionAccuracy: AthletePredictionAccuracy | undefined =
    predictionsData?.accuracy

  const { data: privacyData } = useQuery({
    queryKey: [MEMORY_PRIVACY_QUERY_KEY, authToken],
    queryFn: () => fetchMemoryPrivacySettings(authToken),
    enabled: !!authToken,
  })

  const toggleMemoryMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      updateMemoryPrivacySettings(authToken, { memoryUpdatesEnabled: enabled }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: [MEMORY_PRIVACY_QUERY_KEY] }),
  })

  // Split learned traits into stable facts and inferred observations (#386),
  // each still grouped by category for readability.
  const { factGroups, observationGroups } = useMemo(() => {
    const groupByCategory = (facts: AthleteMemoryFact[]) => {
      const groups = new Map<string, AthleteMemoryFact[]>()
      for (const fact of facts) {
        const list = groups.get(fact.category) ?? []
        list.push(fact)
        groups.set(fact.category, list)
      }
      return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
    }
    const all = data ?? []
    return {
      factGroups: groupByCategory(all.filter((fact) => fact.kind === 'fact')),
      observationGroups: groupByCategory(all.filter((fact) => fact.kind !== 'fact')),
    }
  }, [data])

  const hasTraits = factGroups.length > 0 || observationGroups.length > 0

  const [showAll, setShowAll] = useState<Record<string, boolean>>({})
  const renderCategoryGroups = (list: string, groups: [string, AthleteMemoryFact[]][]) => {
    const facts = groups.flatMap(([, inCategory]) => inCategory)
    const shown = showAll[list] ? facts : facts.slice(0, TRAITS_SHOWN)
    return (
      <>
        <ul className="divide-y divide-gray-100">
          {shown.map((fact) => (
            <TraitRow key={fact.id} fact={fact} token={authToken} />
          ))}
        </ul>
        {facts.length > TRAITS_SHOWN && (
          <button
            type="button"
            className={`${textButton} mt-1`}
            onClick={() => setShowAll((open) => ({ ...open, [list]: !open[list] }))}
          >
            {showAll[list] ? t('Show fewer') : t('Show all {count}', { count: facts.length })}
          </button>
        )}
      </>
    )
  }
  const isExpertMode = useAppStore((s) => s.isExpertMode)

  const memoryEnabled = privacyData?.memoryUpdatesEnabled ?? true

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6 space-y-6">
      <div>
        <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Learned Athlete Traits')}</h3>
        <p className="text-sm text-gray-500 mb-4">
          {t('Patterns the coach has learned about you. Review, correct, confirm, or remove anything that looks wrong — changes are reflected in future coach advice.')}
        </p>

        {isLoading && <p className="text-sm text-gray-400">{t('Loading traits…')}</p>}
        {isError && (
          <p className="text-sm text-red-600">{t('Could not load learned traits.')}</p>
        )}
        {!isLoading && !isError && !hasTraits && (
          <p className="text-sm text-gray-500">
            {t('No learned traits yet. Patterns your coach notices while you chat show up here.')}
          </p>
        )}

        <div className="space-y-5">
          {factGroups.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-900">{t('Facts')}</h4>
              {renderCategoryGroups('facts', factGroups)}
            </div>
          )}
          {observationGroups.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-900">{t('Observations')}</h4>
              {renderCategoryGroups('observations', observationGroups)}
            </div>
          )}
        </div>
      </div>

      {isExpertMode && hypotheses && hypotheses.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Working Hypotheses')}</h3>
          <p className="text-sm text-gray-500 mb-4">
            {t("Testable ideas the coach is tracking but hasn't confirmed yet. Confirm one to turn it into a learned trait, or dismiss it if it doesn't hold up.")}
          </p>
          <ul className="divide-y divide-gray-100">
            {hypotheses.map((hypothesis) => (
              <HypothesisRow
                key={hypothesis.id}
                hypothesis={hypothesis}
                token={authToken}
              />
            ))}
          </ul>
        </div>
      )}

      {isExpertMode && openQuestions && openQuestions.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Open Questions')}</h3>
          <p className="text-sm text-gray-500 mb-4">
            {t("Things the coach can't answer yet and is actively tracking. Each shows the evidence so far and what it still needs. Questions close on their own once enough evidence exists — or mark one answered or dismiss it.")}
          </p>
          <ul className="divide-y divide-gray-100">
            {openQuestions.map((question) => (
              <OpenQuestionRow
                key={question.id}
                question={question}
                token={authToken}
              />
            ))}
          </ul>
        </div>
      )}

      {isExpertMode && experiments && experiments.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Suggested Experiments')}</h3>
          <p className="text-sm text-gray-500 mb-4">
            {t('Where unsure, your coach suggests a small experiment to settle the question with data instead of guessing. Run one, then mark it done or dismiss it.')}
          </p>
          <ul className="divide-y divide-gray-100">
            {experiments.map((experiment) => (
              <ExperimentRow
                key={experiment.id}
                experiment={experiment}
                token={authToken}
              />
            ))}
          </ul>
        </div>
      )}

      {isExpertMode && predictions && predictions.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Predictions')}</h3>
          <p className="text-sm text-gray-500 mb-4">
            {t('To measure the quality of the coaching, your coach makes checkable predictions and later scores them against what actually happened.')}
            {predictionAccuracy && predictionAccuracy.evaluated > 0 && (
              <>
                {' '}
                {slots(
                  t('So far {count} evaluated predictions were correct ({percent}% accuracy).', {
                    percent: Math.round((predictionAccuracy.accuracy ?? 0) * 100),
                  }),
                  {
                    count: (
                      <span key="count" className="font-medium text-gray-700">
                        {t('{correct} of {evaluated}', {
                          correct: predictionAccuracy.correct,
                          evaluated: predictionAccuracy.evaluated,
                        })}
                      </span>
                    ),
                  },
                )}
              </>
            )}
          </p>
          <ul className="divide-y divide-gray-100">
            {predictions.map((prediction) => (
              <PredictionRow
                key={prediction.id}
                prediction={prediction}
                token={authToken}
              />
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-gray-100 pt-5">
        <h4 className="text-sm font-semibold text-gray-800 mb-3">{t('Privacy Controls')}</h4>

        <div className="flex items-center justify-between py-2">
          <div>
            <p className="text-sm text-gray-700 font-medium">{t('Learn from conversations')}</p>
            <p className="text-sm text-gray-500 mt-0.5">
              {t('When enabled, your coach updates the memory after each chat. When disabled, no new patterns are stored and existing memory is not used in coach responses.')}
            </p>
          </div>
          <button
            role="switch"
            aria-checked={memoryEnabled}
            onClick={() => toggleMemoryMutation.mutate(!memoryEnabled)}
            disabled={toggleMemoryMutation.isPending}
            // 40 px to hit on a phone, the track inside it (ai-trainer-ops#270).
            className="group inline-flex min-h-10 min-w-10 shrink-0 cursor-pointer items-center justify-center rounded-full focus:outline-hidden disabled:opacity-50"
          >
            <span
              className={`relative inline-flex h-6 w-11 rounded-full border-2 border-transparent transition-colors duration-200 group-focus:ring-2 group-focus:ring-amber-500 group-focus:ring-offset-2 ${
                memoryEnabled ? 'bg-amber-500' : 'bg-gray-200'
              }`}
            >
              <span
                className={`inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                  memoryEnabled ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </span>
          </button>
        </div>

        {!memoryEnabled && (
          <p className="text-sm text-gray-500 mt-1">
            {t('Memory updates are disabled. The coach will not learn from new conversations and will not use stored patterns in responses.')}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * The coach's memory as a file, or cleared: under "Your data" beside the
 * account export and the danger zone, not in the coach's section
 * (ai-trainer-ops#217). The switch to stop learning stays with the traits.
 */
export function MemoryDataSettings() {
  const authToken = useAuthToken()
  const queryClient = useQueryClient()
  const t = useT()
  const [exportError, setExportError] = useState('')

  const clearMemoryMutation = useMutation({
    mutationFn: () => clearAllMemory(authToken),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [ATHLETE_TRAITS_QUERY_KEY] })
      queryClient.invalidateQueries({ queryKey: [ATHLETE_HYPOTHESES_QUERY_KEY] })
      queryClient.invalidateQueries({
        queryKey: [ATHLETE_OPEN_QUESTIONS_QUERY_KEY],
      })
      queryClient.invalidateQueries({
        queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY],
      })
      queryClient.invalidateQueries({ queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY] })
    },
  })

  const handleClearAll = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(
        t('Clear all coaching memory? Your coach will forget everything learned about you. This cannot be undone.')
      )
    ) {
      return
    }
    clearMemoryMutation.mutate()
  }

  const handleExport = async () => {
    setExportError('')
    try {
      const data = await exportMemory(authToken)
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'coaching-memory-export.json'
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setExportError('Export failed. Please try again.')
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t("Coach's memory")}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Download what your coach has learned about you as a file, or clear the whole memory.')}
      </p>
      <div className="flex flex-wrap gap-2">
        <button onClick={handleExport} className={button}>
          {t('Export my memory')}
        </button>
        <button onClick={handleClearAll} disabled={clearMemoryMutation.isPending} className={deleteButton}>
          {t('Clear all memory')}
        </button>
      </div>
      {clearMemoryMutation.isSuccess && (
        <p className="text-sm text-gray-500 mt-2">{t('All coaching memory cleared.')}</p>
      )}
      {exportError && <p className="text-sm text-red-600 mt-2">{t(exportError)}</p>}
    </div>
  )
}
