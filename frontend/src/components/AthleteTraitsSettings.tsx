import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, ThumbsDown, Trash2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { button, deleteButton, textButton } from './ui'
import {
  clearAllMemory,
  completeValidationExperiment,
  confirmAthleteHypothesis,
  confirmAthleteMemoryFact,
  deleteAthleteHypothesis,
  deleteAthleteMemoryFact,
  deleteAthletePrediction,
  deleteValidationExperiment,
  dismissValidationExperiment,
  answerAthleteOpenQuestion,
  deleteAthleteOpenQuestion,
  dismissAthleteOpenQuestion,
  exportMemory,
  fetchAthleteHypotheses,
  fetchAthleteMemoryFacts,
  fetchAthleteOpenQuestions,
  fetchAthletePredictions,
  fetchMemoryPrivacySettings,
  fetchValidationExperiments,
  markPredictionCorrect,
  markPredictionIncorrect,
  refuteAthleteHypothesis,
  updateAthleteMemoryFact,
  updateMemoryPrivacySettings,
  type AthleteExperiment,
  type AthleteHypothesis,
  type AthleteMemoryFact,
  type AthleteOpenQuestion,
  type AthletePrediction,
  type AthletePredictionAccuracy,
} from '../services/user'
import { localeTag, slots, useLanguage, useT, type Language } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'

export const ATHLETE_TRAITS_QUERY_KEY = 'athlete-memory-facts'
export const ATHLETE_HYPOTHESES_QUERY_KEY = 'athlete-hypotheses'
export const ATHLETE_OPEN_QUESTIONS_QUERY_KEY = 'athlete-open-questions'
export const VALIDATION_EXPERIMENTS_QUERY_KEY = 'validation-experiments'
export const ATHLETE_PREDICTIONS_QUERY_KEY = 'athlete-predictions'
const MEMORY_PRIVACY_QUERY_KEY = 'memory-privacy-settings'

// Five traits per list, then "Show all": a long memory pushed the rest of
// Settings far down (owner's review, 2026-10-23).
export const TRAITS_SHOWN = 5

function formatDate(iso: string, language: Language): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(localeTag(language), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function TraitRow({
  fact,
  token,
}: {
  fact: AthleteMemoryFact
  token: string
}) {
  const queryClient = useQueryClient()
  const t = useT()
  // Stored in English for the engine, shown in the interface language (ai-trainer-ops#215).
  const planText = usePlanText()
  const language = useLanguage()
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(fact.fact)
  const [error, setError] = useState('')

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: [ATHLETE_TRAITS_QUERY_KEY] })

  const saveMutation = useMutation({
    mutationFn: (value: string) =>
      updateAthleteMemoryFact(token, fact.id, { fact: value }),
    onSuccess: () => {
      setIsEditing(false)
      setError('')
      invalidate()
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to save change'),
  })

  const confirmMutation = useMutation({
    mutationFn: () => confirmAthleteMemoryFact(token, fact.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to confirm trait'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAthleteMemoryFact(token, fact.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to delete trait'),
  })

  const isBusy =
    saveMutation.isPending || confirmMutation.isPending || deleteMutation.isPending
  const isConfirmed = fact.status === 'user_confirmed'
  const needsValidation = fact.status === 'needs_validation'

  const handleSave = () => {
    const trimmed = draft.trim()
    if (!trimmed) {
      setError('Trait text cannot be empty')
      return
    }
    // Unchanged, so the stored English text stays: the editor shows the
    // translation, and saving it would swap the engine's text for it.
    if (trimmed === planText(fact.fact).trim()) {
      setIsEditing(false)
      return
    }
    saveMutation.mutate(trimmed)
  }

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t('Delete this learned trait? The coach will forget it.'))
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="py-2.5">
      {isEditing ? (
        <div className="space-y-2">
          <textarea
            aria-label={t('Edit trait')}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="w-full text-sm border border-gray-300 rounded-lg px-2.5 py-1.5 focus:border-amber-500 focus:ring-amber-500"
          />
          <div className="flex items-center gap-2">
            <button onClick={handleSave} disabled={isBusy} className={button}>
              {t('Save')}
            </button>
            <button
              onClick={() => {
                setDraft(fact.fact)
                setIsEditing(false)
                setError('')
              }}
              disabled={isBusy}
              className={textButton}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-gray-900">{planText(fact.fact)}</p>
            {needsValidation && fact.contradictionNote && (
              <p className="text-sm text-gray-600 mt-1">
                {planText(fact.contradictionNote)}
              </p>
            )}
            {/* The state in words; date and confidence are an expert's detail. */}
            {(isConfirmed || needsValidation || isExpertMode) && (
              <p className="text-sm text-gray-500 mt-1">
                {isConfirmed && <span className="mr-2">{t('Confirmed')}</span>}
                {needsValidation && <span className="mr-2">{t('Needs validation')}</span>}
                {isExpertMode &&
                  t('Updated {date} · confidence {percent}%', {
                    date: formatDate(fact.updatedAt, language),
                    percent: Math.round(fact.confidence * 100),
                  })}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {!isConfirmed && (
              <button
                onClick={() => confirmMutation.mutate()}
                disabled={isBusy}
                title={t('Confirm this trait')}
                aria-label={t('Confirm trait')}
                className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
              >
                <CheckCircle2 size={15} />
              </button>
            )}
            <button
              onClick={() => {
                setDraft(planText(fact.fact))
                setIsEditing(true)
              }}
              disabled={isBusy}
              title={t('Edit this trait')}
              aria-label={t('Edit trait')}
              className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
            >
              <Pencil size={15} />
            </button>
            <button
              onClick={handleDelete}
              disabled={isBusy}
              title={t('Delete this trait')}
              aria-label={t('Delete trait')}
              className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}

function HypothesisRow({
  hypothesis,
  token,
}: {
  hypothesis: AthleteHypothesis
  token: string
}) {
  const queryClient = useQueryClient()
  const t = useT()
  const planText = usePlanText()
  const [error, setError] = useState('')

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [ATHLETE_HYPOTHESES_QUERY_KEY] })
    // Confirming a hypothesis promotes it into a learned trait, so refresh both.
    queryClient.invalidateQueries({ queryKey: [ATHLETE_TRAITS_QUERY_KEY] })
  }

  const confirmMutation = useMutation({
    mutationFn: () => confirmAthleteHypothesis(token, hypothesis.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to confirm hypothesis'),
  })

  const refuteMutation = useMutation({
    mutationFn: () => refuteAthleteHypothesis(token, hypothesis.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to dismiss hypothesis'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAthleteHypothesis(token, hypothesis.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to delete hypothesis'),
  })

  const isBusy =
    confirmMutation.isPending || refuteMutation.isPending || deleteMutation.isPending

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t('Delete this hypothesis? The coach will stop tracking it.'))
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{planText(hypothesis.statement)}</p>
          {hypothesis.rationale && (
            <p className="text-sm text-gray-500 mt-1">{planText(hypothesis.rationale)}</p>
          )}
          <p className="text-xs text-gray-400 mt-1">
            <span className="mr-2">{t('Needs validation')}</span>
            {t(hypothesis.evidenceCount === 1 ? 'confidence {percent}% · {count} observation' : 'confidence {percent}% · {count} observations', {
              percent: Math.round(hypothesis.confidence * 100),
              count: hypothesis.evidenceCount,
            })}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => confirmMutation.mutate()}
            disabled={isBusy}
            title={t('Confirm this hypothesis — it becomes a learned trait')}
            aria-label={t('Confirm hypothesis')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => refuteMutation.mutate()}
            disabled={isBusy}
            title={t('Dismiss this hypothesis')}
            aria-label={t('Dismiss hypothesis')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title={t('Delete this hypothesis')}
            aria-label={t('Delete hypothesis')}
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}

function OpenQuestionRow({
  question,
  token,
}: {
  question: AthleteOpenQuestion
  token: string
}) {
  const queryClient = useQueryClient()
  const t = useT()
  const planText = usePlanText()
  const [error, setError] = useState('')

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: [ATHLETE_OPEN_QUESTIONS_QUERY_KEY],
    })

  const answerMutation = useMutation({
    mutationFn: () => answerAthleteOpenQuestion(token, question.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to update question'),
  })

  const dismissMutation = useMutation({
    mutationFn: () => dismissAthleteOpenQuestion(token, question.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to dismiss question'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAthleteOpenQuestion(token, question.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to delete question'),
  })

  const isBusy =
    answerMutation.isPending ||
    dismissMutation.isPending ||
    deleteMutation.isPending

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t('Delete this open question? The coach will stop tracking it.'))
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{planText(question.question)}</p>
          {question.evidence && (
            <p className="text-sm text-gray-500 mt-1">
              <span className="font-medium">{t('Evidence:')}</span> {planText(question.evidence)}
            </p>
          )}
          {question.needs && (
            <p className="text-sm text-gray-500 mt-1">
              <span className="font-medium">{t('Needs:')}</span> {planText(question.needs)}
            </p>
          )}
          <p className="text-xs text-gray-400 mt-1">
            {t(question.evidenceCount === 1 ? '{count} observation' : '{count} observations', { count: question.evidenceCount })}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => answerMutation.mutate()}
            disabled={isBusy}
            title={t('Mark this question as answered')}
            aria-label={t('Answer question')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => dismissMutation.mutate()}
            disabled={isBusy}
            title={t('Dismiss this question')}
            aria-label={t('Dismiss question')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title={t('Delete this question')}
            aria-label={t('Delete question')}
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}

function ExperimentRow({
  experiment,
  token,
}: {
  experiment: AthleteExperiment
  token: string
}) {
  const queryClient = useQueryClient()
  const t = useT()
  const planText = usePlanText()
  const [error, setError] = useState('')

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY],
    })

  const completeMutation = useMutation({
    mutationFn: () => completeValidationExperiment(token, experiment.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to update experiment'),
  })

  const dismissMutation = useMutation({
    mutationFn: () => dismissValidationExperiment(token, experiment.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to dismiss experiment'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteValidationExperiment(token, experiment.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to delete experiment'),
  })

  const isBusy =
    completeMutation.isPending ||
    dismissMutation.isPending ||
    deleteMutation.isPending

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t('Delete this experiment? The coach will stop suggesting it.'))
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{planText(experiment.protocol)}</p>
          <p className="text-sm text-gray-500 mt-1">{planText(experiment.question)}</p>
          {experiment.rationale && (
            <p className="text-xs text-gray-400 mt-1">{planText(experiment.rationale)}</p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => completeMutation.mutate()}
            disabled={isBusy}
            title={t('Mark this experiment as done')}
            aria-label={t('Complete experiment')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => dismissMutation.mutate()}
            disabled={isBusy}
            title={t('Dismiss this experiment')}
            aria-label={t('Dismiss experiment')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title={t('Delete this experiment')}
            aria-label={t('Delete experiment')}
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}

function PredictionRow({
  prediction,
  token,
}: {
  prediction: AthletePrediction
  token: string
}) {
  const queryClient = useQueryClient()
  const t = useT()
  const [error, setError] = useState('')

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY],
    })

  const correctMutation = useMutation({
    mutationFn: () => markPredictionCorrect(token, prediction.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to update prediction'),
  })

  const incorrectMutation = useMutation({
    mutationFn: () => markPredictionIncorrect(token, prediction.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to update prediction'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAthletePrediction(token, prediction.id),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to delete prediction'),
  })

  const isBusy =
    correctMutation.isPending ||
    incorrectMutation.isPending ||
    deleteMutation.isPending

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t('Delete this prediction? The coach will stop tracking it.'))
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{prediction.prediction}</p>
          <p className="text-sm text-gray-500 mt-1">
            {t('Confirmed if: {outcome}', { outcome: prediction.expectedOutcome })}
          </p>
          {prediction.actualOutcome && (
            <p className="text-xs text-gray-400 mt-1">
              {t('Outcome: {outcome}', { outcome: prediction.actualOutcome })}
            </p>
          )}
          <p className="text-xs text-gray-400 mt-1">
            {t('confidence {percent}%', { percent: Math.round(prediction.confidence * 100) })}
            {prediction.horizon ? ` · ${t('check by {horizon}', { horizon: prediction.horizon })}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => correctMutation.mutate()}
            disabled={isBusy}
            title={t('This prediction came true')}
            aria-label={t('Mark prediction correct')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => incorrectMutation.mutate()}
            disabled={isBusy}
            title={t('This prediction did not hold — confidence reduced')}
            aria-label={t('Mark prediction incorrect')}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title={t('Delete this prediction')}
            aria-label={t('Delete prediction')}
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}

export default function AthleteTraitsSettings() {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const t = useT()

  const { data, isLoading, isError } = useQuery({
    queryKey: [ATHLETE_TRAITS_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteMemoryFacts(authToken!),
    enabled: !!authToken,
  })

  const { data: hypotheses } = useQuery({
    queryKey: [ATHLETE_HYPOTHESES_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteHypotheses(authToken!),
    enabled: !!authToken,
  })

  const { data: openQuestions } = useQuery({
    queryKey: [ATHLETE_OPEN_QUESTIONS_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteOpenQuestions(authToken!),
    enabled: !!authToken,
  })

  const { data: experiments } = useQuery({
    queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY, authToken],
    queryFn: () => fetchValidationExperiments(authToken!),
    enabled: !!authToken,
  })

  const { data: predictionsData } = useQuery({
    queryKey: [ATHLETE_PREDICTIONS_QUERY_KEY, authToken],
    queryFn: () => fetchAthletePredictions(authToken!),
    enabled: !!authToken,
  })
  const predictions = predictionsData?.predictions
  const predictionAccuracy: AthletePredictionAccuracy | undefined =
    predictionsData?.accuracy

  const { data: privacyData } = useQuery({
    queryKey: [MEMORY_PRIVACY_QUERY_KEY, authToken],
    queryFn: () => fetchMemoryPrivacySettings(authToken!),
    enabled: !!authToken,
  })

  const toggleMemoryMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      updateMemoryPrivacySettings(authToken!, { memoryUpdatesEnabled: enabled }),
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
            <TraitRow key={fact.id} fact={fact} token={authToken!} />
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
                token={authToken!}
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
                token={authToken!}
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
                token={authToken!}
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
                token={authToken!}
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
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-hidden focus:ring-2 focus:ring-amber-500 focus:ring-offset-2 disabled:opacity-50 ${
              memoryEnabled ? 'bg-amber-500' : 'bg-gray-200'
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                memoryEnabled ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
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
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const t = useT()
  const [exportError, setExportError] = useState('')

  const clearMemoryMutation = useMutation({
    mutationFn: () => clearAllMemory(authToken!),
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
      const data = await exportMemory(authToken!)
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
