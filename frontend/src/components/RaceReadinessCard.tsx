import { useQuery } from '@tanstack/react-query'
import { Target, TrendingUp, Zap, Calendar, Info } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { fetchReadinessScore } from '../services/ai'
import type { ReadinessScore, ReasoningSource } from '../services/ai'

// Knowledge-source labels for recommendation reasoning (issue #377). Each
// bullet is tagged so the athlete can tell a personal observation about
// themselves apart from general sports science and the coach's read of the
// numbers.
const REASONING_SOURCE_META: Record<
  ReasoningSource,
  { label: string; className: string }
> = {
  personal_observation: {
    label: 'Personal observation',
    className: 'bg-purple-50 text-purple-600 border border-purple-100',
  },
  scientific_evidence: {
    label: 'Scientific evidence',
    className: 'bg-blue-50 text-blue-600 border border-blue-100',
  },
  coach_inference: {
    label: 'Coach inference',
    className: 'bg-gray-100 text-gray-600 border border-gray-200',
  },
}

// ---------------------------------------------------------------------------
// Explanation panel — shown to the right of the score
// ---------------------------------------------------------------------------

function ReadinessExplainer() {
  return (
    <div className="bg-gray-50 border border-gray-100 rounded-lg p-3 text-xs text-gray-600 space-y-1.5 min-w-[180px]">
      <div className="flex items-center gap-1.5 mb-1">
        <Info size={12} className="text-gray-400 flex-shrink-0" />
        <span className="font-semibold text-gray-700">How it works</span>
      </div>
      <ul className="space-y-1 list-disc list-inside leading-snug">
        <li><span className="font-medium">CTL</span> — 42-day fitness load (higher = fitter)</li>
        <li><span className="font-medium">ATL</span> — 7-day fatigue load (lower = fresher)</li>
        <li><span className="font-medium">TSB</span> = CTL − ATL (your &ldquo;form&rdquo;)</li>
        <li>Score blends form (65 %) + fitness (35 %)</li>
        <li>Optimal TSB for racing: <span className="font-medium">+5 to +15</span></li>
        <li>Score ≥ 65 = Race Ready; ≥ 80 = Peak Form</li>
        <li>Taper 7–14 days before race to reach peak TSB</li>
      </ul>
    </div>
  )
}

function ScoreRing({ score }: { score: number }) {
  const radius = 36
  const circumference = 2 * Math.PI * radius
  const progress = Math.min(100, Math.max(0, score))
  const dashOffset = circumference - (progress / 100) * circumference

  const color =
    score >= 75 ? '#22c55e' : score >= 50 ? '#f59e0b' : score >= 25 ? '#f97316' : '#ef4444'

  return (
    <div className="relative flex items-center justify-center" style={{ width: 88, height: 88 }}>
      <svg width="88" height="88" className="-rotate-90">
        <circle cx="44" cy="44" r={radius} stroke="#e5e7eb" strokeWidth="8" fill="none" />
        <circle
          cx="44"
          cy="44"
          r={radius}
          stroke={color}
          strokeWidth="8"
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.5s ease' }}
        />
      </svg>
      <span className="absolute text-lg font-bold text-gray-900">{Math.round(score)}</span>
    </div>
  )
}

function statusLabel(score: number): string {
  if (score >= 80) return 'Peak Form'
  if (score >= 65) return 'Race Ready'
  if (score >= 50) return 'Building'
  if (score >= 35) return 'Fatigued'
  return 'Rest Needed'
}

function tsbLabel(tsb: number): string {
  if (tsb > 10) return 'Fresh'
  if (tsb > -5) return 'Neutral'
  if (tsb > -20) return 'Tired'
  return 'Very Tired'
}

function ReadinessMetric({
  label,
  value,
  sub,
  icon,
  color,
}: {
  label: string
  value: string
  sub?: string
  icon: React.ReactNode
  color: string
}) {
  return (
    <div className="flex items-start gap-2">
      <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0 ${color}`}>
        {icon}
      </div>
      <div>
        <p className="text-xs text-gray-500">{label}</p>
        <p className="text-sm font-bold text-gray-900">
          {value}
          {sub && <span className="font-normal text-gray-500 text-xs ml-0.5">{sub}</span>}
        </p>
      </div>
    </div>
  )
}

function ReadinessContent({ data }: { data: ReadinessScore }) {
  const tsbText = `${data.tsb >= 0 ? '+' : ''}${data.tsb.toFixed(1)}`
  const status = statusLabel(data.score)

  return (
    <div className="space-y-4">
      {/* Score + countdown row */}
      <div className="flex items-center gap-4">
        <ScoreRing score={data.score} />
        <div>
          <p className="text-base font-bold text-gray-900">{status}</p>
          <p className="text-xs text-gray-500">Readiness score</p>
          {data.daysUntilRace > 0 && (
            <div className="mt-1 inline-flex items-center gap-1 bg-purple-100 text-purple-800 rounded-full px-2 py-0.5 text-xs font-semibold">
              <Calendar size={11} />
              {data.daysUntilRace}d to race
            </div>
          )}
          {data.daysUntilRace === 0 && data.raceDate && (
            <div className="mt-1 inline-flex items-center gap-1 bg-green-100 text-green-800 rounded-full px-2 py-0.5 text-xs font-semibold">
              🏁 Race day!
            </div>
          )}
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-3">
        <ReadinessMetric
          label="Fitness (CTL)"
          value={data.ctl.toFixed(1)}
          icon={<TrendingUp size={14} />}
          color="text-blue-600 bg-blue-50"
        />
        <ReadinessMetric
          label="Fatigue (ATL)"
          value={data.atl.toFixed(1)}
          icon={<Zap size={14} />}
          color="text-orange-600 bg-orange-50"
        />
        <ReadinessMetric
          label="Form (TSB)"
          value={tsbText}
          sub={` · ${tsbLabel(data.tsb)}`}
          icon={<Target size={14} />}
          color="text-purple-600 bg-purple-50"
        />
        <ReadinessMetric
          label="Form score"
          value={`${data.formScore.toFixed(0)} / 100`}
          icon={<Target size={14} />}
          color="text-green-600 bg-green-50"
        />
      </div>

      {/* Projection row */}
      {data.projectedScore != null && data.daysUntilRace > 0 && (
        <div className="border-t border-gray-100 pt-3">
          <p className="text-xs font-semibold text-gray-500 mb-2">Projected at race day</p>
          <div className="flex items-center gap-4">
            <ScoreRing score={data.projectedScore} />
            <div className="space-y-1 text-xs text-gray-600">
              <p>
                <span className="font-semibold">Score:</span>{' '}
                {Math.round(data.projectedScore)} ({statusLabel(data.projectedScore)})
              </p>
              {data.projectedCtl != null && (
                <p>
                  <span className="font-semibold">CTL:</span> {data.projectedCtl.toFixed(1)}
                </p>
              )}
              {data.projectedTsb != null && (
                <p>
                  <span className="font-semibold">TSB:</span>{' '}
                  {data.projectedTsb >= 0 ? '+' : ''}
                  {data.projectedTsb.toFixed(1)} ({tsbLabel(data.projectedTsb)})
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      <p className="text-xs text-gray-400">
        TSB &lt; −20 = fatigue · TSB +5 to +15 = peak race form
      </p>

      {/* Recommendations */}
      {data.recommendations.length > 0 && (
        <div className="border-t border-gray-100 pt-3">
          <p className="text-xs font-semibold text-gray-500 mb-1.5">What to do next</p>
          <ul className="space-y-2.5">
            {data.recommendations.map((rec) => (
              <li key={rec.recommendation} className="text-xs text-gray-700">
                <div className="flex items-start gap-1.5">
                  <span className="text-purple-400 mt-0.5">•</span>
                  <span className="font-medium">{rec.recommendation}</span>
                </div>
                {rec.reasoning.length > 0 && (
                  <ul className="mt-1 ml-4 space-y-1">
                    {rec.reasoning.map((why) => {
                      const meta = REASONING_SOURCE_META[why.source]
                      return (
                        <li
                          key={`${why.source}:${why.text}`}
                          className="flex items-start gap-1.5 text-[11px] text-gray-500"
                        >
                          <span className="text-gray-300 mt-0.5">–</span>
                          <span>
                            <span
                              className={`mr-1.5 rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide ${meta.className}`}
                            >
                              {meta.label}
                            </span>
                            {why.text}
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function RaceReadinessCard() {
  const authToken = useAppStore((s) => s.authToken)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['readiness-score', authToken],
    queryFn: () => fetchReadinessScore(authToken!),
    enabled: !!authToken,
    staleTime: 60 * 1000,        // consider stale after 1 minute
    refetchInterval: 60 * 1000,  // auto-refetch every minute
  })

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-7 h-7 rounded-md bg-purple-50 flex items-center justify-center">
          <Target size={15} className="text-purple-600" />
        </div>
        <h3 className="text-sm font-bold text-gray-800">Race-Day Readiness</h3>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 py-4 text-gray-400">
          <div className="w-4 h-4 border-2 border-gray-300 border-t-purple-500 rounded-full animate-spin" />
          <span className="text-xs">Calculating readiness…</span>
        </div>
      )}

      {isError && (
        <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
          Could not load readiness score.
        </p>
      )}

      {data && !isLoading && (
        <div className="flex flex-col md:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <ReadinessContent data={data} />
          </div>
          <ReadinessExplainer />
        </div>
      )}
    </div>
  )
}
