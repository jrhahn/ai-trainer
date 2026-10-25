import { useMemo, useState, type FocusEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Lock, Plus, Unlock, X } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAuthToken } from '../hooks/useAuthToken'
import { fieldLabel, input } from './ui'
import MotivationWeightHistory from './MotivationWeightHistory'
import SaveStatus from './SaveStatus'
import { useSaveStatus } from '../hooks/useSaveStatus'
import { useT } from '../i18n'
import {
  fetchMotivationModel,
  updateMotivationModel,
  type AthleteMotivationModel,
  type MotivationComponent,
  type MotivationEntry,
} from '../services/user'
import { MOTIVATION_MODEL_QUERY_KEY } from '../queries/keys'


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
  const t = useT()
  if (source === 'user_set') {
    return (
      <p className="mt-0.5 text-sm text-gray-500">
        <span className="font-medium text-gray-500">{t('You set this.')}</span>{' '}
        {t('The coach will not overwrite it.')}
      </p>
    )
  }
  return (
    <p className="mt-0.5 text-sm text-gray-500">
      {t('Inferred · {percent} confidence', { percent: formatPercent(confidence) })}
      {snippet ? <> · {t('from “{snippet}”', { snippet })}</> : null}
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
  const t = useT()
  const contradicted = entry.status === 'contradicted'
  return (
    <li
      className={`rounded-lg border p-2 ${
        contradicted ? 'border-gray-300 bg-white' : 'border-gray-200 bg-white'
      }`}
    >
      <div className="flex items-start gap-1.5">
        <div className="flex flex-col">
          <button
            type="button"
            aria-label={t('Move “{text}” up', { text: entry.text })}
            disabled={index === 0}
            onClick={() => onMove(-1)}
            className="text-gray-400 hover:text-gray-900 disabled:opacity-30"
          >
            <ChevronUp size={13} />
          </button>
          <button
            type="button"
            aria-label={t('Move “{text}” down', { text: entry.text })}
            disabled={index === total - 1}
            onClick={() => onMove(1)}
            className="text-gray-400 hover:text-gray-900 disabled:opacity-30"
          >
            <ChevronDown size={13} />
          </button>
        </div>
        <div className="flex-1">
          <input
            value={entry.text}
            aria-label={`${label} ${index + 1}`}
            onChange={(event) => onChange(event.target.value)}
            className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-sm text-gray-800 hover:border-gray-200 focus:border-amber-400 focus:outline-hidden"
          />
          <EvidenceLine
            source={entry.source}
            confidence={entry.confidence}
            snippet={entry.sourceSnippet}
          />
          {contradicted && entry.contradictionNote && (
            <p className="mt-1 text-sm text-gray-600">{entry.contradictionNote}</p>
          )}
        </div>
        <button
          type="button"
          aria-label={t('Remove “{text}”', { text: entry.text })}
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
  const authToken = useAuthToken()
  // The objectives are the short form every athlete needs; the weights the
  // coach learned behind them are model internals (ai-trainer-ops#50).
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const queryClient = useQueryClient()
  const t = useT()

  const { data, isLoading, isError } = useQuery({
    queryKey: [MOTIVATION_MODEL_QUERY_KEY, authToken],
    queryFn: () => fetchMotivationModel(authToken),
    enabled: !!authToken,
  })

  const [draft, setDraft] = useState<AthleteMotivationModel | null>(data ?? null)
  const status = useSaveStatus()

  // The server is the source of truth until the athlete starts editing; once a
  // save lands, the fresh model replaces the draft so inference results and
  // normalised weights are visible rather than the pre-save guess. Taken over
  // while rendering, not in an effect (ai-trainer-ops#286).
  const [draftOf, setDraftOf] = useState(data)
  if (data !== draftOf) {
    setDraftOf(data)
    if (data) setDraft(data)
  }

  const mutation = useMutation({
    mutationFn: (changes: Parameters<typeof updateMotivationModel>[1]) =>
      updateMotivationModel(authToken, changes),
    onSuccess: (saved) => {
      setDraft(saved)
      queryClient.invalidateQueries({ queryKey: [MOTIVATION_MODEL_QUERY_KEY] })
    },
  })

  const dirty = useMemo(
    () => !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data),
    [draft, data]
  )

  if (isLoading) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-xs">
        <p className="text-sm text-gray-400">{t('Loading your objective…')}</p>
      </div>
    )
  }
  if (isError || !draft) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-xs">
        <p className="text-sm text-red-600">{t('Could not load your objective.')}</p>
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

  const save = (model: AthleteMotivationModel) =>
    status.run(() =>
      mutation.mutateAsync({
        primaryObjective: model.primaryObjective,
        // Blank rows are a half-finished edit, not an objective.
        secondaryObjectives: model.secondaryObjectives
          .filter((entry) => entry.text.trim())
          .map((entry) => ({ ...entry, text: entry.text.trim() })),
        constraints: model.constraints
          .filter((entry) => entry.text.trim())
          .map((entry) => ({ ...entry, text: entry.text.trim() })),
        utilityWeights: model.utilityWeights,
        pinnedWeights: model.pinnedWeights,
      })
    )

  // Saved when focus leaves the card (ai-trainer-ops#93): the objectives,
  // their order and the weights are one model, so moving between them is one
  // edit, and only leaving it is the moment to save.
  const commit = (event: FocusEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    if (!dirty || mutation.isPending) return
    void save(draft)
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
            // At once: the button that had focus goes with its row, so no
            // blur would ever report the removal.
            onRemove={() => {
              const next = { ...draft, [field]: draft[field].filter((_, i) => i !== index) }
              setDraft(next)
              void save(next)
            }}
            onMove={(delta) => moveEntry(field, index, delta)}
          />
        ))}
      </ul>
      <button
        type="button"
        onClick={() => addEntry(field)}
        className="mt-1.5 inline-flex items-center gap-1 text-sm font-medium text-amber-600 hover:text-amber-700"
      >
        <Plus size={14} aria-hidden="true" /> {addLabel}
      </button>
    </>
  )

  return (
    <div className="space-y-5 rounded-2xl border border-gray-100 bg-white p-6 shadow-xs" onBlur={commit}>
      <div>
        <h3 className="mb-1 text-base font-semibold text-gray-900">{t('What You Train For')}</h3>
        <p className="text-sm text-gray-500">
          {t('What your coach takes as your goal decides which sessions come first. Your coach never overwrites what you set here.')}
        </p>
      </div>

      <div>
        <label htmlFor="primary-objective" className={fieldLabel}>
          {t('Primary objective')}
        </label>
        <input
          id="primary-objective"
          value={draft.primaryObjective}
          placeholder={t('e.g. Maximize enjoyable technical trail riding')}
          onChange={(event) =>
            setDraft({ ...draft, primaryObjective: event.target.value })
          }
          className={input}
        />
        {draft.primaryObjective && (
          <EvidenceLine
            source={draft.primaryObjectiveSource}
            confidence={draft.primaryObjectiveConfidence}
            snippet={draft.primaryObjectiveSnippet}
          />
        )}
      </div>

      {/* Fields one under the other, a label each: no rules between them and
          no explanation under every label (owner's review, 2026-10-23,
          "Gefrickel"). */}
      <div>
        <p className={fieldLabel}>{t('Also matters')}</p>
        {renderEntries('secondaryObjectives', t('Objective'), t('Add objective'))}
      </div>

      <div>
        <p className={fieldLabel}>{t('Never trade away')}</p>
        {renderEntries('constraints', t('Constraint'), t('Add constraint'))}
      </div>

      {isExpertMode && (
        <>
          <div className="border-t border-gray-100 pt-4">
            <h4 className="mb-1 text-sm font-semibold text-gray-900">{t('Balance')}</h4>
            <p className="mb-3 text-sm text-gray-500">
              {t('How much each of these counts when the coach picks between two sessions. Pin one to fix it — the coach will keep learning the rest around it. Percentages are re-balanced to 100% when they are saved.')}
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
                      aria-label={t(pinned ? 'Unpin {component}' : 'Pin {component}', { component: t(COMPONENT_LABELS[component]) })}
                      aria-pressed={pinned}
                      className={pinned ? 'text-amber-600' : 'text-gray-300 hover:text-gray-500'}
                    >
                      {pinned ? <Lock size={12} /> : <Unlock size={12} />}
                    </button>
                    <span className="w-24 shrink-0 text-sm text-gray-600">
                      {t(COMPONENT_LABELS[component])}
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={Math.round(value * 100)}
                      aria-label={t('{component} weight', { component: t(COMPONENT_LABELS[component]) })}
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

          {/* The balance moves on its own, so it owes the athlete an account of
              itself — right underneath the numbers it explains (#566). */}
          <MotivationWeightHistory componentLabels={COMPONENT_LABELS} />

          {Object.keys(draft.modalityAffinity ?? {}).length > 0 && (
            <div className="border-t border-gray-100 pt-4">
              <h4 className="mb-1 text-sm font-semibold text-gray-900">
                {t('How you like to ride')}
              </h4>
              <p className="mb-2 text-sm text-gray-500">
                {t('Learned from what you actually ride, not from what was planned. The coach uses it to pick between two sessions that would train you equally well.')}
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {Object.entries(draft.modalityAffinity).map(([modality, affinity]) => (
                  <li
                    key={modality}
                    className="rounded-full border border-gray-200 px-2 py-0.5 text-sm text-gray-600"
                  >
                    {t(MODALITY_LABELS[modality] ?? modality)} · {formatPercent(affinity)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <SaveStatus state={status.state} />
    </div>
  )
}
