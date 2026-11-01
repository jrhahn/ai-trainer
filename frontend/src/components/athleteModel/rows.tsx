import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Trash2 } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { button, textButton } from '../ui'
import {
  answerAthleteOpenQuestion,
  completeValidationExperiment,
  confirmAthleteHypothesis,
  confirmAthleteMemoryFact,
  deleteAthleteHypothesis,
  deleteAthleteMemoryFact,
  deleteAthleteOpenQuestion,
  deleteAthletePrediction,
  deleteValidationExperiment,
  dismissAthleteOpenQuestion,
  dismissValidationExperiment,
  markPredictionCorrect,
  markPredictionIncorrect,
  refuteAthleteHypothesis,
  updateAthleteMemoryFact,
  type AthleteExperiment,
  type AthleteHypothesis,
  type AthleteMemoryFact,
  type AthleteOpenQuestion,
  type AthletePrediction,
} from '../../services/user'
import { localeTag, useLanguage, useT, type Language } from '../../i18n'
import { usePlanText } from '../../hooks/usePlanTranslations'
import {
  ATHLETE_HYPOTHESES_QUERY_KEY,
  ATHLETE_OPEN_QUESTIONS_QUERY_KEY,
  ATHLETE_PREDICTIONS_QUERY_KEY,
  ATHLETE_TRAITS_QUERY_KEY,
  VALIDATION_EXPERIMENTS_QUERY_KEY,
} from '../../queries/keys'
import ReviewableRow from './ReviewableRow'
import { PendingTranslation } from '../TranslationNote'

// The rows of the coach's memory in Settings (ai-trainer-ops#274: out of
// `AthleteTraitsSettings`). A trait can be edited; the other four are answered.

/** The confidence from which the coach relies on a trait it was not told to
 *  keep: `rider_identity.PATTERN_MIN_CONFIDENCE` and the bar in
 *  `prompts.athlete_memory_facts_section`, both 0.5. Below it a trait is a
 *  guess, and says so with the words it came from (ai-trainer-ops#216). */
const RELIES_ON_CONFIDENCE = 0.5

function formatDate(iso: string, language: Language): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(localeTag(language), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function TraitRow({
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
  // Only an active trait: a confirmed one is relied on, one waiting for
  // validation says so, and the others are not in this list.
  const isGuess = fact.status === 'active' && fact.confidence < RELIES_ON_CONFIDENCE

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
            className="w-full text-base sm:text-sm border border-gray-300 rounded-lg px-2.5 py-1.5 focus:border-amber-500 focus:ring-amber-500"
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
            <PendingTranslation texts={[fact.fact, needsValidation ? fact.contradictionNote : null]}>
              <p className="text-sm text-gray-900">{planText(fact.fact)}</p>
              {needsValidation && fact.contradictionNote && (
                <p className="text-sm text-gray-600 mt-1">
                  {planText(fact.contradictionNote)}
                </p>
              )}
            </PendingTranslation>
            {/* The state in words; date and confidence are an expert's detail. */}
            {(isConfirmed || needsValidation || isGuess || isExpertMode) && (
              <p className="text-sm text-gray-500 mt-1">
                {isConfirmed && <span className="mr-2">{t('Confirmed')}</span>}
                {needsValidation && <span className="mr-2">{t('Needs validation')}</span>}
                {isGuess && <span className="mr-2">{t('A guess, the coach does not rely on it yet')}</span>}
                {isExpertMode &&
                  t('Updated {date} · confidence {percent}%', {
                    date: formatDate(fact.updatedAt, language),
                    percent: Math.round(fact.confidence * 100),
                  })}
              </p>
            )}
            {/* The athlete's own words, so a guess from one remark can be
                recognised and deleted. They are not translated. */}
            {isGuess && fact.sourceSnippet && (
              <p className="text-sm text-gray-500 mt-1">{t('From: “{words}”', { words: fact.sourceSnippet })}</p>
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

export function HypothesisRow({ hypothesis, token }: { hypothesis: AthleteHypothesis; token: string }) {
  const t = useT()
  const planText = usePlanText()
  return (
    <ReviewableRow
      // Confirming a hypothesis promotes it into a learned trait, so refresh both.
      refresh={[ATHLETE_HYPOTHESES_QUERY_KEY, ATHLETE_TRAITS_QUERY_KEY]}
      yes={{
        run: () => confirmAthleteHypothesis(token, hypothesis.id),
        failure: 'Failed to confirm hypothesis',
        title: t('Confirm this hypothesis — it becomes a learned trait'),
        label: t('Confirm hypothesis'),
      }}
      no={{
        run: () => refuteAthleteHypothesis(token, hypothesis.id),
        failure: 'Failed to dismiss hypothesis',
        title: t('Dismiss this hypothesis'),
        label: t('Dismiss hypothesis'),
      }}
      remove={{
        run: () => deleteAthleteHypothesis(token, hypothesis.id),
        failure: 'Failed to delete hypothesis',
        title: t('Delete this hypothesis'),
        label: t('Delete hypothesis'),
        confirm: t('Delete this hypothesis? The coach will stop tracking it.'),
      }}
    >
      <PendingTranslation texts={[hypothesis.statement, hypothesis.rationale]}>
        <p className="text-sm text-gray-900">{planText(hypothesis.statement)}</p>
        {hypothesis.rationale && <p className="text-sm text-gray-500 mt-1">{planText(hypothesis.rationale)}</p>}
      </PendingTranslation>
      <p className="text-xs text-gray-400 mt-1">
        <span className="mr-2">{t('Needs validation')}</span>
        {t(hypothesis.evidenceCount === 1 ? 'confidence {percent}% · {count} observation' : 'confidence {percent}% · {count} observations', {
          percent: Math.round(hypothesis.confidence * 100),
          count: hypothesis.evidenceCount,
        })}
      </p>
    </ReviewableRow>
  )
}

export function OpenQuestionRow({ question, token }: { question: AthleteOpenQuestion; token: string }) {
  const t = useT()
  const planText = usePlanText()
  return (
    <ReviewableRow
      refresh={[ATHLETE_OPEN_QUESTIONS_QUERY_KEY]}
      yes={{
        run: () => answerAthleteOpenQuestion(token, question.id),
        failure: 'Failed to update question',
        title: t('Mark this question as answered'),
        label: t('Answer question'),
      }}
      no={{
        run: () => dismissAthleteOpenQuestion(token, question.id),
        failure: 'Failed to dismiss question',
        title: t('Dismiss this question'),
        label: t('Dismiss question'),
      }}
      remove={{
        run: () => deleteAthleteOpenQuestion(token, question.id),
        failure: 'Failed to delete question',
        title: t('Delete this question'),
        label: t('Delete question'),
        confirm: t('Delete this open question? The coach will stop tracking it.'),
      }}
    >
      <PendingTranslation texts={[question.question, question.evidence, question.needs]}>
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
      </PendingTranslation>
      <p className="text-xs text-gray-400 mt-1">
        {t(question.evidenceCount === 1 ? '{count} observation' : '{count} observations', { count: question.evidenceCount })}
      </p>
    </ReviewableRow>
  )
}

export function ExperimentRow({ experiment, token }: { experiment: AthleteExperiment; token: string }) {
  const t = useT()
  const planText = usePlanText()
  return (
    <ReviewableRow
      refresh={[VALIDATION_EXPERIMENTS_QUERY_KEY]}
      yes={{
        run: () => completeValidationExperiment(token, experiment.id),
        failure: 'Failed to update experiment',
        title: t('Mark this experiment as done'),
        label: t('Complete experiment'),
      }}
      no={{
        run: () => dismissValidationExperiment(token, experiment.id),
        failure: 'Failed to dismiss experiment',
        title: t('Dismiss this experiment'),
        label: t('Dismiss experiment'),
      }}
      remove={{
        run: () => deleteValidationExperiment(token, experiment.id),
        failure: 'Failed to delete experiment',
        title: t('Delete this experiment'),
        label: t('Delete experiment'),
        confirm: t('Delete this experiment? The coach will stop suggesting it.'),
      }}
    >
      <PendingTranslation texts={[experiment.protocol, experiment.question, experiment.rationale]}>
        <p className="text-sm text-gray-900">{planText(experiment.protocol)}</p>
        <p className="text-sm text-gray-500 mt-1">{planText(experiment.question)}</p>
        {experiment.rationale && <p className="text-xs text-gray-400 mt-1">{planText(experiment.rationale)}</p>}
      </PendingTranslation>
    </ReviewableRow>
  )
}

export function PredictionRow({ prediction, token }: { prediction: AthletePrediction; token: string }) {
  const t = useT()
  const planText = usePlanText()
  return (
    <ReviewableRow
      refresh={[ATHLETE_PREDICTIONS_QUERY_KEY]}
      yes={{
        run: () => markPredictionCorrect(token, prediction.id),
        failure: 'Failed to update prediction',
        title: t('This prediction came true'),
        label: t('Mark prediction correct'),
      }}
      no={{
        run: () => markPredictionIncorrect(token, prediction.id),
        failure: 'Failed to update prediction',
        title: t('This prediction did not hold — confidence reduced'),
        label: t('Mark prediction incorrect'),
      }}
      remove={{
        run: () => deleteAthletePrediction(token, prediction.id),
        failure: 'Failed to delete prediction',
        title: t('Delete this prediction'),
        label: t('Delete prediction'),
        confirm: t('Delete this prediction? The coach will stop tracking it.'),
      }}
    >
      <PendingTranslation
        texts={[prediction.prediction, prediction.expectedOutcome, prediction.actualOutcome, prediction.horizon]}
      >
        <p className="text-sm text-gray-900">{planText(prediction.prediction)}</p>
        <p className="text-sm text-gray-500 mt-1">
          {t('Confirmed if: {outcome}', { outcome: planText(prediction.expectedOutcome) })}
        </p>
        {prediction.actualOutcome && (
          <p className="text-xs text-gray-400 mt-1">{t('Outcome: {outcome}', { outcome: planText(prediction.actualOutcome) })}</p>
        )}
      </PendingTranslation>
      <p className="text-xs text-gray-400 mt-1">
        {t('confidence {percent}%', { percent: Math.round(prediction.confidence * 100) })}
        {prediction.horizon ? ` · ${t('check by {horizon}', { horizon: planText(prediction.horizon) })}` : ''}
      </p>
    </ReviewableRow>
  )
}
