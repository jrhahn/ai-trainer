import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Lock,
  Plus,
  Target,
  Unlock,
  X,
} from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  fetchMotivationModel,
  updateMotivationModel,
  type AthleteMotivationModel,
  type MotivationComponent,
  type MotivationEntry,
} from '../services/user'

export const MOTIVATION_MODEL_QUERY_KEY = 'motivation-model'

// The axes a training option is scored on, in the order the backend defines
// them. Labelled here rather than in the API: the athlete reads "Enjoyment",
// the planner reads "enjoyment".
const COMPONENT_LABELS: Record<MotivationComponent, string> = {
  enjoyment: 'Enjoyment',
  adaptation: 'Fitness gains',
  consistency: 'Consistency',
  health: 'Health',
  race_performance: 'Race results',
}
const COMPONENTS = Object.keys(COMPONENT_LABELS) as MotivationComponent[]

const MODALITY_LABELS: Record<string, string> = {
  road: 'Road',
  mtb: 'MTB',
  gravel: 'Gravel',
  indoor: 'Indoor',
  gym: 'Gym',
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** Confidence and provenance, so an inferred claim never reads as a fact. */
function EvidenceLine({
  source,
  confidence,
  snippet,
}: {
  source: string
  confidence: number
  snippet?: string
}) {
  if (source === 'user_set') {
    return (
      <p className="mt-0.5 text-[11px] text-gray-400">
        <span className="font-medium text-gray-500">You set this.</span> The coach
        will not overwrite it.
      </p>
    )
  }
  return (
    <p className="mt-0.5 text-[11px] text-gray-400">
      Inferred · {formatPercent(confidence)} confidence
      {snippet ? <> · from “{snippet}”</> : null}
    </p>
  )
}

function EntryRow({
  entry,
  index,
  total,
  label,
  onChange,
  onRemove,
  onMove,
}: {
  entry: MotivationEntry
  index: number
  total: number
  /** What this list holds, so the input is named for what it actually is. */
  label: string
  onChange: (text: string) => void
  onRemove: () => void
  onMove: (delta: number) => void
}) {
  const contradicted = entry.status === 'contradicted'
  return (
    <li
      className={`rounded-lg border p-2 ${
        contradicted ? 'border-amber-200 bg-amber-50' : 'border-gray-200 bg-white'
      }`}
    >
      <div className="flex items-start gap-1.5">
        <div className="flex flex-col">
          <button
            type="button"
            aria-label={`Move “${entry.text}” up`}
            disabled={index === 0}
            onClick={() => onMove(-1)}
            className="text-gray-400 hover:text-amber-600 disabled:opacity-30"
          >
            <ChevronUp size={13} />
          </button>
          <button
            type="button"
            aria-label={`Move “${entry.text}” down`}
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            className="text-gray-400 hover:text-amber-600 disabled:opacity-30"
          >
            <ChevronDown size={13} />
          </button>
        </div>
        <div className="flex-1">
          <input
            value={entry.text}
            aria-label={`${label} ${index + 1}`}
            onChange={(event) => onChange(event.target.value)}
            className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-sm text-gray-800 hover:border-gray-200 focus:border-amber-400 focus:outline-none"
          />
          <EvidenceLine
            source={entry.source}
            confidence={entry.confidence}
            snippet={entry.sourceSnippet}
          />
          {contradicted && entry.contradictionNote && (
            <p className="mt-1 flex items-start gap-1 text-[11px] text-amber-700">
              <AlertTriangle size={11} className="mt-px shrink-0" />
              <span>{entry.contradictionNote}</span>
            </p>
          )}
        </div>
        <button
          type="button"
          aria-label={`Remove “${entry.text}”`}
          onClick={onRemove}
          className="text-gray-300 hover:text-red-500"
        >
          <X size={14} />
        </button>
      </div>
    </li>
  )
}

export default function MotivationModelSettings() {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: [MOTIVATION_MODEL_QUERY_KEY, authToken],
    queryFn: () => fetchMotivationModel(authToken!),
    enabled: !!authToken,
  })

  const [draft, setDraft] = useState<AthleteMotivationModel | null>(null)
  const [saveError, setSaveError] = useState('')

  // The server is the source of truth until the athlete starts editing; once a
  // save lands, the fresh model replaces the draft so inference results and
  // normalised weights are visible rather than the pre-save guess.
  useEffect(() => {
    if (data) setDraft(data)
  }, [data])

  const mutation = useMutation({
    mutationFn: (changes: Parameters<typeof updateMotivationModel>[1]) =>
      updateMotivationModel(authToken!, changes),
    onSuccess: (saved) => {
      setDraft(saved)
      setSaveError('')
      queryClient.invalidateQueries({ queryKey: [MOTIVATION_MODEL_QUERY_KEY] })
    },
    onError: () => setSaveError('Could not save. Please try again.'),
  })

  const dirty = useMemo(
    () => !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data),
    [draft, data]
  )

  if (isLoading) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <p className="text-sm text-gray-400">Loading your objective…</p>
      </div>
    )
  }
  if (isError || !draft) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <p className="text-sm text-red-600">Could not load your objective.</p>
      </div>
    )
  }

  const patchEntries = (
    field: 'secondaryObjectives' | 'constraints',
    next: MotivationEntry[]
  ) => setDraft({ ...draft, [field]: next })

  const moveEntry = (
    field: 'secondaryObjectives' | 'constraints',
    index: number,
    delta: number
  ) => {
    const next = [...draft[field]]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    patchEntries(field, next)
  }

  const addEntry = (field: 'secondaryObjectives' | 'constraints') =>
    patchEntries(field, [
      ...draft[field],
      {
        text: '',
        confidence: 1,
        source: 'user_set',
        sourceSnippet: '',
        status: 'active',
        contradictionNote: null,
        firstObservedAt: null,
        lastConfirmedAt: null,
      },
    ])

  const togglePin = (component: MotivationComponent) => {
    const pinned = draft.pinnedWeights.includes(component)
      ? draft.pinnedWeights.filter((c) => c !== component)
      : [...draft.pinnedWeights, component]
    setDraft({ ...draft, pinnedWeights: pinned })
  }

  const setWeight = (component: MotivationComponent, value: number) =>
    setDraft({
      ...draft,
      utilityWeights: { ...draft.utilityWeights, [component]: value },
    })

  const handleSave = () => {
    setSaveError('')
    mutation.mutate({
      primaryObjective: draft.primaryObjective,
      // Blank rows are a half-finished edit, not an objective.
      secondaryObjectives: draft.secondaryObjectives
        .filter((entry) => entry.text.trim())
        .map((entry) => ({ ...entry, text: entry.text.trim() })),
      constraints: draft.constraints
        .filter((entry) => entry.text.trim())
        .map((entry) => ({ ...entry, text: entry.text.trim() })),
      utilityWeights: draft.utilityWeights,
      pinnedWeights: draft.pinnedWeights,
    })
  }

  const renderEntries = (
    field: 'secondaryObjectives' | 'constraints',
    label: string,
    addLabel: string
  ) => (
    <>
      <ul className="space-y-1.5">
        {draft[field].map((entry, index) => (
          <EntryRow
            key={`${field}-${index}`}
            entry={entry}
            index={index}
            total={draft[field].length}
            label={label}
            onChange={(text) => {
              const next = [...draft[field]]
              // An edited entry is the athlete's own wording from here on.
              next[index] = { ...next[index], text, source: 'user_set' }
              patchEntries(field, next)
            }}
            onRemove={() =>
              patchEntries(
                field,
                draft[field].filter((_, i) => i !== index)
              )
            }
            onMove={(delta) => moveEntry(field, index, delta)}
          />
        ))}
      </ul>
      <button
        type="button"
        onClick={() => addEntry(field)}
        className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-amber-600 hover:text-amber-700"
      >
        <Plus size={12} /> {addLabel}
      </button>
    </>
  )

  return (
    <div className="space-y-5 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
      <div>
        <h2 className="mb-1 flex items-center gap-1.5 text-base font-bold text-gray-900">
          <Target size={16} /> What You Train For
        </h2>
        <p className="text-xs text-gray-500">
          Your fitness serves something. This is what the coach believes that is —
          it decides which sessions get recommended and how they are explained to
          you. Correct anything that is wrong; the coach never overwrites what you
          set here.
        </p>
      </div>

      <div>
        <label
          htmlFor="primary-objective"
          className="mb-1 block text-sm font-semibold text-gray-700"
        >
          Primary objective
        </label>
        <input
          id="primary-objective"
          value={draft.primaryObjective}
          placeholder="e.g. Maximize enjoyable technical trail riding"
          onChange={(event) =>
            setDraft({ ...draft, primaryObjective: event.target.value })
          }
          className="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm text-gray-800 focus:border-amber-400 focus:outline-none"
        />
        {draft.primaryObjective ? (
          <EvidenceLine
            source={draft.primaryObjectiveSource}
            confidence={draft.primaryObjectiveConfidence}
            snippet={draft.primaryObjectiveSnippet}
          />
        ) : (
          <p className="mt-0.5 text-[11px] text-gray-400">
            Nothing inferred yet. Until you set one, the coach explains its advice
            in plain training terms rather than guessing what you want.
          </p>
        )}
      </div>

      <div className="border-t border-gray-100 pt-4">
        <h3 className="mb-1 text-sm font-semibold text-gray-700">
          Also matters
        </h3>
        <p className="mb-2 text-xs text-gray-500">
          Secondary objectives, most important first.
        </p>
        {renderEntries('secondaryObjectives', 'Objective', 'Add objective')}
      </div>

      <div className="border-t border-gray-100 pt-4">
        <h3 className="mb-1 text-sm font-semibold text-gray-700">
          Never trade away
        </h3>
        <p className="mb-2 text-xs text-gray-500">
          Constraints rule options out entirely — they are not weighed against
          anything.
        </p>
        {renderEntries('constraints', 'Constraint', 'Add constraint')}
      </div>

      <div className="border-t border-gray-100 pt-4">
        <h3 className="mb-1 text-sm font-semibold text-gray-700">Balance</h3>
        <p className="mb-3 text-xs text-gray-500">
          How much each of these counts when the coach picks between two sessions.
          Pin one to fix it — the coach will keep learning the rest around it.
          Percentages are re-balanced to 100% when you save.
        </p>
        <div className="space-y-2.5">
          {COMPONENTS.map((component) => {
            const pinned = draft.pinnedWeights.includes(component)
            const value = draft.utilityWeights[component] ?? 0
            return (
              <div key={component} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => togglePin(component)}
                  aria-label={`${pinned ? 'Unpin' : 'Pin'} ${COMPONENT_LABELS[component]}`}
                  aria-pressed={pinned}
                  className={pinned ? 'text-amber-600' : 'text-gray-300 hover:text-gray-500'}
                >
                  {pinned ? <Lock size={12} /> : <Unlock size={12} />}
                </button>
                <span className="w-24 shrink-0 text-xs text-gray-600">
                  {COMPONENT_LABELS[component]}
                </span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={Math.round(value * 100)}
                  aria-label={`${COMPONENT_LABELS[component]} weight`}
                  onChange={(event) =>
                    setWeight(component, Number(event.target.value) / 100)
                  }
                  className="h-1 flex-1 accent-amber-500"
                />
                <span className="w-9 shrink-0 text-right text-xs tabular-nums text-gray-500">
                  {formatPercent(value)}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {Object.keys(draft.modalityAffinity ?? {}).length > 0 && (
        <div className="border-t border-gray-100 pt-4">
          <h3 className="mb-1 text-sm font-semibold text-gray-700">
            How you like to ride
          </h3>
          <p className="mb-2 text-xs text-gray-500">
            Learned from what you actually ride, not from what was planned. The
            coach uses it to pick between two sessions that would train you
            equally well.
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {Object.entries(draft.modalityAffinity).map(([modality, affinity]) => (
              <li
                key={modality}
                className="rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-600"
              >
                {MODALITY_LABELS[modality] ?? modality} · {formatPercent(affinity)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3 border-t border-gray-100 pt-4">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || mutation.isPending}
          className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-40"
        >
          {mutation.isPending ? 'Saving…' : 'Save'}
        </button>
        {dirty && !mutation.isPending && (
          <button
            type="button"
            onClick={() => setDraft(data ?? null)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            Discard changes
          </button>
        )}
        {saveError && <span className="text-xs text-red-600">{saveError}</span>}
      </div>
    </div>
  )
}
