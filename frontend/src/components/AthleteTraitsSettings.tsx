import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, Check, CheckCircle2, Pencil, Trash2, X } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  confirmAthleteMemoryFact,
  deleteAthleteMemoryFact,
  fetchAthleteMemoryFacts,
  updateAthleteMemoryFact,
  type AthleteMemoryFact,
} from '../services/user'

const ATHLETE_TRAITS_QUERY_KEY = 'athlete-memory-facts'

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
            <p className="text-[11px] text-gray-400 mt-1">
              {isConfirmed && (
                <span className="inline-flex items-center gap-0.5 text-green-600 font-medium mr-2">
                  <CheckCircle2 size={11} /> Confirmed
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

export default function AthleteTraitsSettings() {
  const authToken = useAppStore((s) => s.authToken)

  const { data, isLoading, isError } = useQuery({
    queryKey: [ATHLETE_TRAITS_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteMemoryFacts(authToken!),
    enabled: !!authToken,
  })

  const grouped = useMemo(() => {
    const groups = new Map<string, AthleteMemoryFact[]>()
    for (const fact of data ?? []) {
      const list = groups.get(fact.category) ?? []
      list.push(fact)
      groups.set(fact.category, list)
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [data])

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
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
  )
}
