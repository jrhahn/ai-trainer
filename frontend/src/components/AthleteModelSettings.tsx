import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, RefreshCw, Save } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  fetchAthleteModel,
  saveAthleteModel,
  type AthleteModel,
  type AthleteModelEdit,
} from '../services/user'
import { refreshAthleteModel } from '../services/ai'

export const ATHLETE_MODEL_QUERY_KEY = 'athlete-model'

/** Qualitative single-line fields: [state key, label, placeholder]. */
const TEXT_FIELDS: Array<[keyof AthleteModelEdit, string, string]> = [
  ['pacingQuality', 'Pacing quality', 'e.g. even pacing on long efforts'],
  ['recoveryAbility', 'Recovery ability', 'e.g. recovers within a day'],
  ['thresholdDurability', 'Threshold durability', 'e.g. holds threshold ~30 min'],
  ['heatTolerance', 'Heat tolerance', 'e.g. fades above 28°C'],
  ['preferredTrainingStyle', 'Preferred training style', 'e.g. structured intervals'],
]

/** List fields edited as one item per line: [state key, label]. */
const LIST_FIELDS: Array<[keyof AthleteModelEdit, string]> = [
  ['strengths', 'Strengths'],
  ['weaknesses', 'Weaknesses'],
  ['riskFactors', 'Risk factors'],
]

function toDraft(model: AthleteModel): AthleteModelEdit {
  return {
    ftpWatts: model.ftpWatts,
    vo2max: model.vo2max,
    pacingQuality: model.pacingQuality,
    recoveryAbility: model.recoveryAbility,
    thresholdDurability: model.thresholdDurability,
    heatTolerance: model.heatTolerance,
    preferredTrainingStyle: model.preferredTrainingStyle,
    strengths: model.strengths,
    weaknesses: model.weaknesses,
    riskFactors: model.riskFactors,
    summary: model.summary,
  }
}

function linesToList(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export default function AthleteModelSettings() {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<AthleteModelEdit | null>(null)
  const [error, setError] = useState('')

  const { data, isLoading, isError } = useQuery({
    queryKey: [ATHLETE_MODEL_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteModel(authToken!),
    enabled: !!authToken,
  })

  // Seed/reseed the editable draft whenever a new server model arrives (initial
  // load, save, or refresh). Adjusting state during render on a changed value is
  // React's recommended alternative to a setState-in-effect.
  const [syncedModel, setSyncedModel] = useState<AthleteModel | undefined>()
  if (data && data !== syncedModel) {
    setSyncedModel(data)
    setDraft(toDraft(data))
  }

  const applyModel = (model: AthleteModel) => {
    queryClient.setQueryData([ATHLETE_MODEL_QUERY_KEY, authToken], model)
  }

  const saveMutation = useMutation({
    mutationFn: (value: AthleteModelEdit) => saveAthleteModel(authToken!, value),
    onSuccess: (model) => {
      setError('')
      applyModel(model)
    },
    onError: () => setError('Could not save the athlete model.'),
  })

  const refreshMutation = useMutation({
    mutationFn: () => refreshAthleteModel(authToken!),
    onSuccess: (model) => {
      setError('')
      applyModel(model)
    },
    onError: () => setError('Could not refresh from training history.'),
  })

  const confidencePct = useMemo(
    () => (data ? Math.round(data.confidence * 100) : 0),
    [data]
  )

  const setField = <K extends keyof AthleteModelEdit>(
    key: K,
    value: AthleteModelEdit[K]
  ) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
  }

  const handleSave = () => {
    if (draft) saveMutation.mutate(draft)
  }

  const busy = saveMutation.isPending || refreshMutation.isPending

  return (
    <section className="bg-white rounded-2xl border border-gray-100 p-5 mt-6">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-600" />
          <h3 className="text-base font-semibold text-gray-900">
            Long-term athlete model
          </h3>
        </div>
        <button
          type="button"
          onClick={() => refreshMutation.mutate()}
          disabled={!authToken || busy}
          className="flex items-center gap-1 bg-gray-50 border border-gray-200 text-gray-700 rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-gray-100 disabled:opacity-50"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`}
          />
          Refresh from training
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Durable physiology &amp; performance the coach uses as stable knowledge.
        The coach refreshes this from your training history; you can correct it.
      </p>

      {isLoading && <p className="text-sm text-gray-400">Loading…</p>}
      {isError && (
        <p className="text-sm text-red-600">Could not load the athlete model.</p>
      )}

      {draft && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">
              <span className="block text-gray-600 mb-1">FTP (watts)</span>
              <input
                type="number"
                value={draft.ftpWatts ?? ''}
                onChange={(e) =>
                  setField(
                    'ftpWatts',
                    e.target.value === '' ? null : Number(e.target.value)
                  )
                }
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </label>
            <label className="text-sm">
              <span className="block text-gray-600 mb-1">VO₂ max (ml/kg/min)</span>
              <input
                type="number"
                step="0.1"
                value={draft.vo2max ?? ''}
                onChange={(e) =>
                  setField(
                    'vo2max',
                    e.target.value === '' ? null : Number(e.target.value)
                  )
                }
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </label>
          </div>

          {TEXT_FIELDS.map(([key, label, placeholder]) => (
            <label key={key} className="text-sm block">
              <span className="block text-gray-600 mb-1">{label}</span>
              <input
                type="text"
                value={(draft[key] as string) ?? ''}
                placeholder={placeholder}
                onChange={(e) => setField(key, e.target.value as never)}
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </label>
          ))}

          {LIST_FIELDS.map(([key, label]) => (
            <label key={key} className="text-sm block">
              <span className="block text-gray-600 mb-1">
                {label} <span className="text-gray-400">(one per line)</span>
              </span>
              <textarea
                rows={3}
                value={(draft[key] as string[]).join('\n')}
                onChange={(e) => setField(key, linesToList(e.target.value) as never)}
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </label>
          ))}

          <label className="text-sm block">
            <span className="block text-gray-600 mb-1">Summary</span>
            <textarea
              rows={2}
              value={draft.summary}
              onChange={(e) => setField('summary', e.target.value)}
              className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-200"
            />
          </label>

          {error && <p className="text-xs text-red-600">{error}</p>}

          <div className="flex items-center justify-between">
            <p className="text-[11px] text-gray-400">
              Coach confidence: {confidencePct}%
              {data?.updatedAt ? ` · updated ${formatDate(data.updatedAt)}` : ''}
            </p>
            <button
              type="button"
              onClick={handleSave}
              disabled={busy}
              className="flex items-center gap-1 bg-blue-600 text-white rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-blue-700 disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              Save
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
