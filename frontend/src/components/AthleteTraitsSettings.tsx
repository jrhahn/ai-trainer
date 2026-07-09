import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Beaker,
  Brain,
  Check,
  CheckCircle2,
  Download,
  FlaskConical,
  Pencil,
  ThumbsDown,
  Trash2,
  X,
} from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  clearAllMemory,
  completeValidationExperiment,
  confirmAthleteHypothesis,
  confirmAthleteMemoryFact,
  deleteAthleteHypothesis,
  deleteAthleteMemoryFact,
  deleteValidationExperiment,
  dismissValidationExperiment,
  exportMemory,
  fetchAthleteHypotheses,
  fetchAthleteMemoryFacts,
  fetchMemoryPrivacySettings,
  fetchValidationExperiments,
  refuteAthleteHypothesis,
  updateAthleteMemoryFact,
  updateMemoryPrivacySettings,
  type AthleteExperiment,
  type AthleteHypothesis,
  type AthleteMemoryFact,
} from '../services/user'

export const ATHLETE_TRAITS_QUERY_KEY = 'athlete-memory-facts'
export const ATHLETE_HYPOTHESES_QUERY_KEY = 'athlete-hypotheses'
export const VALIDATION_EXPERIMENTS_QUERY_KEY = 'validation-experiments'
const MEMORY_PRIVACY_QUERY_KEY = 'memory-privacy-settings'

/** Turn a stored category slug (e.g. "coaching_risk") into a readable heading. */
function formatCategory(category: string): string {
  const cleaned = category.replace(/_/g, ' ').trim()
  if (!cleaned) return 'General'
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1)
}

function formatDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, {
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
    saveMutation.mutate(trimmed)
  }

  const handleDelete = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm('Delete this learned trait? The coach will forget it.')
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="border border-gray-100 rounded-xl px-3 py-2.5">
      {isEditing ? (
        <div className="space-y-2">
          <textarea
            aria-label="Edit trait"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={isBusy}
              className="flex items-center gap-1 bg-blue-600 text-white rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-blue-700 disabled:opacity-50"
            >
              <Check size={13} /> Save
            </button>
            <button
              onClick={() => {
                setDraft(fact.fact)
                setIsEditing(false)
                setError('')
              }}
              disabled={isBusy}
              className="flex items-center gap-1 bg-gray-50 border border-gray-200 text-gray-600 rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-gray-100 disabled:opacity-50"
            >
              <X size={13} /> Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-gray-900">{fact.fact}</p>
            {needsValidation && fact.contradictionNote && (
              <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-2 py-1 mt-1.5">
                {fact.contradictionNote}
              </p>
            )}
            <p className="text-[11px] text-gray-400 mt-1">
              {isConfirmed && (
                <span className="inline-flex items-center gap-0.5 text-green-600 font-medium mr-2">
                  <CheckCircle2 size={11} /> Confirmed
                </span>
              )}
              {needsValidation && (
                <span className="inline-flex items-center gap-0.5 text-amber-600 font-medium mr-2">
                  <AlertTriangle size={11} /> Needs validation
                </span>
              )}
              Updated {formatDate(fact.updatedAt)}
              {' · '}confidence {Math.round(fact.confidence * 100)}%
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {!isConfirmed && (
              <button
                onClick={() => confirmMutation.mutate()}
                disabled={isBusy}
                title="Confirm this trait"
                aria-label="Confirm trait"
                className="p-1.5 text-gray-400 hover:text-green-600 disabled:opacity-50"
              >
                <CheckCircle2 size={15} />
              </button>
            )}
            <button
              onClick={() => setIsEditing(true)}
              disabled={isBusy}
              title="Edit this trait"
              aria-label="Edit trait"
              className="p-1.5 text-gray-400 hover:text-blue-600 disabled:opacity-50"
            >
              <Pencil size={15} />
            </button>
            <button
              onClick={handleDelete}
              disabled={isBusy}
              title="Delete this trait"
              aria-label="Delete trait"
              className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-xs text-red-600 mt-1.5">{error}</p>}
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
      !window.confirm('Delete this hypothesis? The coach will stop tracking it.')
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="border border-gray-100 rounded-xl px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{hypothesis.statement}</p>
          {hypothesis.rationale && (
            <p className="text-[11px] text-gray-500 mt-1">{hypothesis.rationale}</p>
          )}
          <p className="text-[11px] text-gray-400 mt-1">
            <span className="inline-flex items-center gap-0.5 text-indigo-600 font-medium mr-2">
              <FlaskConical size={11} /> Needs validation
            </span>
            confidence {Math.round(hypothesis.confidence * 100)}%
            {' · '}
            {hypothesis.evidenceCount}{' '}
            {hypothesis.evidenceCount === 1 ? 'observation' : 'observations'}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => confirmMutation.mutate()}
            disabled={isBusy}
            title="Confirm this hypothesis — it becomes a learned trait"
            aria-label="Confirm hypothesis"
            className="p-1.5 text-gray-400 hover:text-green-600 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => refuteMutation.mutate()}
            disabled={isBusy}
            title="Dismiss this hypothesis"
            aria-label="Dismiss hypothesis"
            className="p-1.5 text-gray-400 hover:text-amber-600 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title="Delete this hypothesis"
            aria-label="Delete hypothesis"
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-red-600 mt-1.5">{error}</p>}
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
      !window.confirm('Delete this experiment? The coach will stop suggesting it.')
    ) {
      return
    }
    deleteMutation.mutate()
  }

  return (
    <li className="border border-gray-100 rounded-xl px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{experiment.protocol}</p>
          <p className="text-[11px] text-gray-500 mt-1">{experiment.question}</p>
          {experiment.rationale && (
            <p className="text-[11px] text-gray-400 mt-1">{experiment.rationale}</p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => completeMutation.mutate()}
            disabled={isBusy}
            title="Mark this experiment as done"
            aria-label="Complete experiment"
            className="p-1.5 text-gray-400 hover:text-green-600 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => dismissMutation.mutate()}
            disabled={isBusy}
            title="Dismiss this experiment"
            aria-label="Dismiss experiment"
            className="p-1.5 text-gray-400 hover:text-amber-600 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title="Delete this experiment"
            aria-label="Delete experiment"
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-red-600 mt-1.5">{error}</p>}
    </li>
  )
}

export default function AthleteTraitsSettings() {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const [exportError, setExportError] = useState('')

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

  const { data: experiments } = useQuery({
    queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY, authToken],
    queryFn: () => fetchValidationExperiments(authToken!),
    enabled: !!authToken,
  })

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

  const clearMemoryMutation = useMutation({
    mutationFn: () => clearAllMemory(authToken!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [ATHLETE_TRAITS_QUERY_KEY] })
      queryClient.invalidateQueries({ queryKey: [ATHLETE_HYPOTHESES_QUERY_KEY] })
      queryClient.invalidateQueries({
        queryKey: [VALIDATION_EXPERIMENTS_QUERY_KEY],
      })
    },
  })

  const handleClearAll = () => {
    if (
      typeof window !== 'undefined' &&
      !window.confirm(
        'Clear all coaching memory? The coach will forget everything it has learned about you. This cannot be undone.'
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

  const grouped = useMemo(() => {
    const groups = new Map<string, AthleteMemoryFact[]>()
    for (const fact of data ?? []) {
      const list = groups.get(fact.category) ?? []
      list.push(fact)
      groups.set(fact.category, list)
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [data])

  const memoryEnabled = privacyData?.memoryUpdatesEnabled ?? true

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 space-y-6">
      <div>
        <h2 className="flex items-center gap-1.5 text-base font-bold text-gray-900 mb-1">
          <Brain size={16} /> Learned Athlete Traits
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Patterns the coach has learned about you. Review, correct, confirm, or
          remove anything that looks wrong — changes are reflected in future coach
          advice.
        </p>

        {isLoading && <p className="text-sm text-gray-400">Loading traits…</p>}
        {isError && (
          <p className="text-sm text-red-600">Could not load learned traits.</p>
        )}
        {!isLoading && !isError && grouped.length === 0 && (
          <p className="text-sm text-gray-400">
            No learned traits yet. As you chat with the coach, it will note
            patterns here.
          </p>
        )}

        <div className="space-y-4">
          {grouped.map(([category, facts]) => (
            <div key={category}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1.5">
                {formatCategory(category)}
              </h3>
              <ul className="space-y-2">
                {facts.map((fact) => (
                  <TraitRow key={fact.id} fact={fact} token={authToken!} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {hypotheses && hypotheses.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-gray-900 mb-1">
            <FlaskConical size={16} /> Working Hypotheses
          </h2>
          <p className="text-xs text-gray-500 mb-4">
            Testable ideas the coach is tracking but hasn't confirmed yet. Confirm
            one to turn it into a learned trait, or dismiss it if it doesn't hold
            up.
          </p>
          <ul className="space-y-2">
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

      {experiments && experiments.length > 0 && (
        <div className="border-t border-gray-100 pt-5">
          <h2 className="flex items-center gap-1.5 text-base font-bold text-gray-900 mb-1">
            <Beaker size={16} /> Suggested Experiments
          </h2>
          <p className="text-xs text-gray-500 mb-4">
            Where the coach is unsure, it suggests a small experiment to settle the
            question with data instead of guessing. Run one, then mark it done or
            dismiss it.
          </p>
          <ul className="space-y-2">
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

      <div className="border-t border-gray-100 pt-5">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Privacy Controls</h3>

        <div className="flex items-center justify-between py-2">
          <div>
            <p className="text-sm text-gray-700 font-medium">Learn from conversations</p>
            <p className="text-xs text-gray-500 mt-0.5">
              When enabled, the coach updates its memory after each chat. When
              disabled, no new patterns are stored and existing memory is not
              used in coach responses.
            </p>
          </div>
          <button
            role="switch"
            aria-checked={memoryEnabled}
            onClick={() => toggleMemoryMutation.mutate(!memoryEnabled)}
            disabled={toggleMemoryMutation.isPending}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 ${
              memoryEnabled ? 'bg-blue-600' : 'bg-gray-200'
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
          <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2 mt-1">
            Memory updates are disabled. The coach will not learn from new conversations
            and will not use stored patterns in responses.
          </p>
        )}

        <div className="flex flex-wrap gap-2 mt-4">
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg px-3 py-1.5 hover:bg-gray-50"
          >
            <Download size={13} /> Export my memory
          </button>
          <button
            onClick={handleClearAll}
            disabled={clearMemoryMutation.isPending}
            className="flex items-center gap-1.5 text-xs font-medium text-red-600 border border-red-100 rounded-lg px-3 py-1.5 hover:bg-red-50 disabled:opacity-50"
          >
            <Trash2 size={13} /> Clear all memory
          </button>
        </div>
        {clearMemoryMutation.isSuccess && (
          <p className="text-xs text-green-600 mt-2">All coaching memory cleared.</p>
        )}
        {exportError && <p className="text-xs text-red-600 mt-2">{exportError}</p>}
      </div>
    </div>
  )
}
