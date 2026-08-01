import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, RefreshCw, Target, Lightbulb } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  fetchAthletePerformanceModel,
  refreshAthletePerformanceModel,
} from '../services/ai'
import {
  fetchAthleteHypotheses,
  type AthleteHypothesis,
  type AthletePerformanceAttribute,
  type AthletePerformanceModel,
} from '../services/user'

export const PERFORMANCE_MODEL_QUERY_KEY = 'athlete-performance-model'
export const PERFORMANCE_HYPOTHESES_QUERY_KEY = 'athlete-performance-hypotheses'

const ATTRIBUTE_LABELS: Record<string, string> = {
  ftp: 'Threshold (FTP)',
  map: 'Maximal aerobic power',
  vo2max: 'VO₂max',
  fractional_utilization: 'Fractional utilization',
  aerobic_endurance: 'Aerobic endurance',
  fatigue_resistance: 'Fatigue resistance',
  anaerobic_capacity: 'Anaerobic capacity',
}

const LIMITER_LABELS: Record<string, string> = {
  threshold: 'Threshold utilization',
  vo2max: 'VO₂max / aerobic ceiling',
  endurance_durability: 'Endurance durability',
  insufficient_data: 'Not enough data',
}

function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function confidenceTone(confidence: number): string {
  if (confidence >= 0.66) return 'bg-emerald-500'
  if (confidence >= 0.4) return 'bg-amber-500'
  return 'bg-gray-400'
}

function confidenceLabel(confidence: number): string {
  if (confidence >= 0.66) return 'high confidence'
  if (confidence >= 0.4) return 'moderate confidence'
  return 'low confidence'
}

/** A visually distinct confidence bar so an inference never reads as a hard fact. */
function ConfidenceBar({ confidence }: { confidence: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, confidence)) * 100)
  return (
    <div className="flex items-center gap-1.5" title={`${pct}% — ${confidenceLabel(confidence)}`}>
      <div className="w-16 h-1.5 rounded-full bg-gray-100 overflow-hidden">
        <div
          className={`h-full rounded-full ${confidenceTone(confidence)}`}
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Confidence ${pct} percent, ${confidenceLabel(confidence)}`}
        />
      </div>
      <span className="text-[11px] text-gray-500 tabular-nums">{pct}%</span>
    </div>
  )
}

function attributeValue(attr: AthletePerformanceAttribute): string {
  if (attr.estimate != null) {
    return `${attr.estimate}${attr.unit ? ` ${attr.unit}` : ''}`
  }
  if (attr.score && attr.score !== 'unknown') {
    return humanize(attr.score)
  }
  return 'Unknown'
}

function AttributeRow({ name, attr }: { name: string; attr: AthletePerformanceAttribute }) {
  const label = ATTRIBUTE_LABELS[name] ?? humanize(name)
  return (
    <div className="py-2 border-b border-gray-50 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-gray-700">{label}</span>
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-gray-900 tabular-nums">
            {attributeValue(attr)}
          </span>
          <ConfidenceBar confidence={attr.confidence} />
        </div>
      </div>
      {attr.evidence.length > 0 && (
        <p className="text-xs text-gray-500 mt-1">
          <span className="text-gray-400">Evidence: </span>
          {attr.evidence.join('; ')}
        </p>
      )}
      {attr.missingInformation.length > 0 && (
        <p className="text-xs text-amber-600 mt-0.5">
          <span className="text-amber-400">Still missing: </span>
          {attr.missingInformation.join('; ')}
        </p>
      )}
    </div>
  )
}

function LimiterHighlight({ model }: { model: AthletePerformanceModel }) {
  const top = model.limiters[0]
  if (!top || top.limiter === 'insufficient_data') return null
  const label = LIMITER_LABELS[top.limiter] ?? humanize(top.limiter)
  return (
    <div className="rounded-xl bg-indigo-50 border border-indigo-100 p-3">
      <div className="flex items-center justify-between gap-3 mb-1">
        <div className="flex items-center gap-1.5">
          <Target className="w-4 h-4 text-indigo-600" />
          <span className="text-sm font-semibold text-indigo-900">
            Likely current limiter: {label}
          </span>
        </div>
        <ConfidenceBar confidence={top.confidence} />
      </div>
      {top.evidence.length > 0 && (
        <p className="text-xs text-indigo-700">
          <span className="text-indigo-400">Why: </span>
          {top.evidence.join('; ')}
        </p>
      )}
      {top.counterEvidence.length > 0 && (
        <p className="text-xs text-gray-500 mt-0.5">
          <span className="text-gray-400">But note: </span>
          {top.counterEvidence.join('; ')}
        </p>
      )}
      {model.recommendations?.sufficient && model.recommendations.hypothesis && (
        <p className="text-xs text-indigo-800 mt-1.5 italic">
          {model.recommendations.hypothesis}
        </p>
      )}
    </div>
  )
}

function HypothesisRow({ hypothesis }: { hypothesis: AthleteHypothesis }) {
  return (
    <div className="rounded-lg border border-gray-100 p-2.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-gray-800">{hypothesis.statement}</p>
        <ConfidenceBar confidence={hypothesis.confidence} />
      </div>
      {hypothesis.evidence.length > 0 && (
        <p className="text-xs text-gray-500 mt-1">
          <span className="text-gray-400">Evidence: </span>
          {hypothesis.evidence.join('; ')}
        </p>
      )}
      {hypothesis.alternativeExplanations.length > 0 && (
        <p className="text-xs text-gray-500 mt-0.5">
          <span className="text-gray-400">Could also be: </span>
          {hypothesis.alternativeExplanations.join('; ')}
        </p>
      )}
    </div>
  )
}

export default function AthletePerformanceModelCard({
  className = '',
}: {
  className?: string
} = {}) {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()

  const modelQuery = useQuery({
    queryKey: [PERFORMANCE_MODEL_QUERY_KEY, authToken],
    queryFn: () => fetchAthletePerformanceModel(authToken!),
    enabled: !!authToken,
  })
  const hypothesesQuery = useQuery({
    queryKey: [PERFORMANCE_HYPOTHESES_QUERY_KEY, authToken],
    queryFn: () => fetchAthleteHypotheses(authToken!),
    enabled: !!authToken,
  })

  const refreshMutation = useMutation({
    mutationFn: () => refreshAthletePerformanceModel(authToken!),
    onSuccess: (model) => {
      queryClient.setQueryData([PERFORMANCE_MODEL_QUERY_KEY, authToken], model)
      queryClient.invalidateQueries({
        queryKey: [PERFORMANCE_HYPOTHESES_QUERY_KEY, authToken],
      })
    },
  })

  const model = modelQuery.data
  const attributes = Object.entries(model?.attributes ?? {})
  // Only surface deterministic performance-model hypotheses here (the editable
  // trait/hypothesis manager owns the rest), and only while still open.
  const hypotheses = (hypothesesQuery.data ?? []).filter(
    (h) => h.category === 'performance_model' && h.status === 'proposed'
  )
  const hasModel = attributes.length > 0

  return (
    <section className={`bg-white rounded-2xl border border-gray-100 p-5 ${className}`}>
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-indigo-600" />
          <h3 className="text-base font-semibold text-gray-900">
            Coach&rsquo;s understanding
          </h3>
        </div>
        <button
          type="button"
          onClick={() => refreshMutation.mutate()}
          disabled={!authToken || refreshMutation.isPending}
          className="flex items-center gap-1 bg-gray-50 border border-gray-200 text-gray-700 rounded-lg px-3 py-1.5 text-xs font-semibold hover:bg-gray-100 disabled:opacity-50"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`}
          />
          Refresh
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        What the coach infers about your physiology from your own rides. These are
        estimates with a confidence, not measured facts &mdash; ask the coach
        &ldquo;why?&rdquo; to see the reasoning.
      </p>

      {modelQuery.isLoading && <p className="text-sm text-gray-400">Loading…</p>}
      {modelQuery.isError && (
        <p className="text-sm text-red-600">Could not load the performance model.</p>
      )}

      {!modelQuery.isLoading && !modelQuery.isError && !hasModel && (
        <p className="text-sm text-gray-400">
          Not enough training data yet. Once a few rides with power are analysed,
          the coach will start building a model of your strengths and limiter.
        </p>
      )}

      {hasModel && model && (
        <div className="space-y-4">
          <LimiterHighlight model={model} />

          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Inferred attributes
            </h4>
            <div>
              {attributes.map(([name, attr]) => (
                <AttributeRow key={name} name={name} attr={attr} />
              ))}
            </div>
          </div>

          {hypotheses.length > 0 && (
            <div>
              <h4 className="flex items-center gap-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
                Working hypotheses
              </h4>
              <div className="space-y-2">
                {hypotheses.map((h) => (
                  <HypothesisRow key={h.id} hypothesis={h} />
                ))}
              </div>
            </div>
          )}

          {model.updatedAt && (
            <p className="text-[11px] text-gray-400">
              Derived from {model.derivedFromRides} rides
              {model.sourceWindowDays ? ` over ~${model.sourceWindowDays} days` : ''}.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
