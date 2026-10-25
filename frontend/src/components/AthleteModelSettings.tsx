import { useMemo, useState, type FocusEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, RefreshCw } from 'lucide-react'
import { useAuthToken } from '../hooks/useAuthToken'
import {
  fetchAthleteModel,
  saveAthleteModel,
  type AthleteModel,
  type AthleteModelEdit,
} from '../services/user'
import { refreshAthleteModel } from '../services/ai'
import { localeTag, useLanguage, useT, type Language } from '../i18n'
import { ATHLETE_MODEL_QUERY_KEY } from '../queries/keys'


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

function formatDate(iso: string | null, language: Language): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(localeTag(language), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export default function AthleteModelSettings() {
  const authToken = useAuthToken()
  const queryClient = useQueryClient()
  const t = useT()
  const language = useLanguage()
  const [draft, setDraft] = useState<AthleteModelEdit | null>(null)
  // List fields are edited as raw multi-line text and only split into arrays on
  // save, so a just-typed newline isn't stripped mid-edit.
  const [listText, setListText] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  const { data, isLoading, isError } = useQuery({
    queryKey: [ATHLETE_MODEL_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteModel(authToken),
    enabled: !!authToken,
  })

  // Seed/reseed the editable draft whenever a new server model arrives (initial
  // load, save, or refresh). Adjusting state during render on a changed value is
  // React's recommended alternative to a setState-in-effect.
  const [syncedModel, setSyncedModel] = useState<AthleteModel | undefined>()
  if (data && data !== syncedModel) {
    setSyncedModel(data)
    setDraft(toDraft(data))
    setListText(
      Object.fromEntries(
        LIST_FIELDS.map(([key]) => [key, (data[key] as string[]).join('\n')])
      )
    )
  }

  const applyModel = (model: AthleteModel) => {
    queryClient.setQueryData([ATHLETE_MODEL_QUERY_KEY, authToken], model)
  }

  const saveMutation = useMutation({
    mutationFn: (value: AthleteModelEdit) => saveAthleteModel(authToken, value),
    onSuccess: (model) => {
      setError('')
      applyModel(model)
    },
    onError: () => setError('Could not save the athlete model.'),
  })

  const refreshMutation = useMutation({
    mutationFn: () => refreshAthleteModel(authToken),
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

  const busy = saveMutation.isPending || refreshMutation.isPending

  // Saved when focus leaves the form, and only when something changed
  // (ai-trainer-ops#93): settings on this page save themselves.
  const commit = (event: FocusEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    if (!draft || !data || busy) return
    const payload: AthleteModelEdit = { ...draft }
    for (const [key] of LIST_FIELDS) {
      payload[key] = linesToList(listText[key] ?? '') as never
    }
    if (JSON.stringify(payload) === JSON.stringify(toDraft(data))) return
    saveMutation.mutate(payload)
  }

  return (
    <section className="bg-white rounded-2xl border border-gray-100 p-5 mt-6">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-600" />
          <h3 className="text-base font-semibold text-gray-900">
            {t('Long-term athlete model')}
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
          {t('Refresh from training')}
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        {t('Durable physiology & performance the coach uses as stable knowledge. The coach refreshes this from your training history; you can correct it.')}
      </p>

      {isLoading && <p className="text-sm text-gray-400">{t('Loading…')}</p>}
      {isError && (
        <p className="text-sm text-red-600">{t('Could not load the athlete model.')}</p>
      )}

      {draft && (
        <div className="space-y-4" onBlur={commit}>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">
              <span className="block text-gray-600 mb-1">{t('FTP (watts)')}</span>
              <input
                type="number"
                value={draft.ftpWatts ?? ''}
                onChange={(e) =>
                  setField(
                    'ftpWatts',
                    e.target.value === '' ? null : Number(e.target.value)
                  )
                }
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:ring-2 focus:ring-blue-200"
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
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:ring-2 focus:ring-blue-200"
              />
            </label>
          </div>

          {TEXT_FIELDS.map(([key, label, placeholder]) => (
            <label key={key} className="text-sm block">
              <span className="block text-gray-600 mb-1">{t(label)}</span>
              <input
                type="text"
                value={(draft[key] as string) ?? ''}
                placeholder={t(placeholder)}
                onChange={(e) => setField(key, e.target.value as never)}
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:ring-2 focus:ring-blue-200"
              />
            </label>
          ))}

          {LIST_FIELDS.map(([key, label]) => (
            <label key={key} className="text-sm block">
              <span className="block text-gray-600 mb-1">
                {t(label)} <span className="text-gray-400">{t('(one per line)')}</span>
              </span>
              <textarea
                rows={3}
                value={listText[key] ?? ''}
                onChange={(e) =>
                  setListText((prev) => ({ ...prev, [key]: e.target.value }))
                }
                className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:ring-2 focus:ring-blue-200"
              />
            </label>
          ))}

          <label className="text-sm block">
            <span className="block text-gray-600 mb-1">{t('Summary')}</span>
            <textarea
              rows={2}
              value={draft.summary}
              onChange={(e) => setField('summary', e.target.value)}
              className="w-full text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:ring-2 focus:ring-blue-200"
            />
          </label>

          {error && <p className="text-xs text-red-600">{t(error)}</p>}

          <div className="flex items-center justify-between">
            <p className="text-[11px] text-gray-400">
              {t('Coach confidence: {percent}%', { percent: confidencePct })}
              {data?.updatedAt ? ` · ${t('updated {date}', { date: formatDate(data.updatedAt, language) })}` : ''}
            </p>
            {saveMutation.isPending && <p className="text-xs text-gray-500">{t('Saving…')}</p>}
          </div>
        </div>
      )}
    </section>
  )
}
