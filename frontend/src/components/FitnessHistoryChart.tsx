/**
 * FitnessHistoryChart
 *
 * Renders a simple SVG line chart showing FTP over time, built from the
 * /users/me/fitness-history endpoint. No external charting library required.
 */

import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import { TrendingUp } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { fetchFitnessHistory, type FitnessSnapshot } from '../services/user'

const CHART_W = 400
const CHART_H = 140
const PAD_L = 40
const PAD_R = 12
const PAD_T = 12
const PAD_B = 28

function buildPath(snapshots: FitnessSnapshot[]): { points: { x: number; y: number; s: FitnessSnapshot }[]; path: string } | null {
  const valid = snapshots.filter((s) => s.ftp != null)
  if (valid.length < 2) return null

  const times = valid.map((s) => new Date(s.measuredAt).getTime())
  const ftps = valid.map((s) => s.ftp as number)

  const minT = Math.min(...times)
  const maxT = Math.max(...times)
  const minF = Math.min(...ftps)
  const maxF = Math.max(...ftps)
  const rangeT = maxT - minT || 1
  const rangeF = maxF - minF || 1

  const innerW = CHART_W - PAD_L - PAD_R
  const innerH = CHART_H - PAD_T - PAD_B

  const points = valid.map((s) => ({
    x: PAD_L + ((new Date(s.measuredAt).getTime() - minT) / rangeT) * innerW,
    y: PAD_T + innerH - ((( s.ftp as number) - minF) / rangeF) * innerH,
    s,
  }))

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  return { points, path }
}

export default function FitnessHistoryChart() {
  const authToken = useAppStore((s) => s.authToken)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['fitness-history', authToken],
    queryFn: () => fetchFitnessHistory(authToken!),
    enabled: !!authToken,
    staleTime: 5 * 60 * 1000,
  })

  const snapshots = data ?? []
  const chart = buildPath(snapshots)
  const hasFTP = snapshots.some((s) => s.ftp != null)

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={16} className="text-purple-500" />
        <h3 className="text-sm font-bold text-gray-800">FTP History</h3>
        {snapshots.length > 0 && (
          <span className="ml-auto text-xs text-gray-400">{snapshots.length} data point{snapshots.length !== 1 ? 's' : ''}</span>
        )}
      </div>

      {isLoading && (
        <p className="text-xs text-gray-400 py-4 text-center">Loading history…</p>
      )}

      {isError && (
        <p className="text-xs text-red-500 py-2">Could not load fitness history.</p>
      )}

      {!isLoading && !isError && snapshots.length === 0 && (
        <p className="text-xs text-gray-400 py-4 text-center">
          No data yet — run a Strava analysis or upload a .fit file to start tracking your FTP over time.
        </p>
      )}

      {!isLoading && !isError && snapshots.length > 0 && !hasFTP && (
        <p className="text-xs text-gray-400 py-4 text-center">
          FTP data will appear here after analysis completes.
        </p>
      )}

      {!isLoading && !isError && chart && (
        <div className="overflow-x-auto">
          <svg
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            width="100%"
            style={{ minWidth: 260 }}
            aria-label="FTP progression chart"
          >
            {/* Y-axis grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
              const y = PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - t)
              const valid = snapshots.filter((s) => s.ftp != null)
              const ftps = valid.map((s) => s.ftp as number)
              const minF = Math.min(...ftps)
              const maxF = Math.max(...ftps)
              const label = Math.round(minF + (maxF - minF) * t)
              return (
                <g key={t}>
                  <line x1={PAD_L} x2={CHART_W - PAD_R} y1={y} y2={y} stroke="#f3f4f6" strokeWidth={1} />
                  <text x={PAD_L - 4} y={y + 4} textAnchor="end" fontSize={9} fill="#9ca3af">
                    {label}
                  </text>
                </g>
              )
            })}

            {/* Line */}
            <path d={chart.path} fill="none" stroke="#a855f7" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

            {/* Area fill */}
            {(() => {
              const bottomL = `L${chart.points[chart.points.length - 1].x.toFixed(1)},${(CHART_H - PAD_B).toFixed(1)} L${chart.points[0].x.toFixed(1)},${(CHART_H - PAD_B).toFixed(1)} Z`
              return (
                <path
                  d={chart.path + ' ' + bottomL}
                  fill="#a855f7"
                  fillOpacity={0.08}
                />
              )
            })()}

            {/* Data points */}
            {chart.points.map((p, i) => (
              <circle key={i} cx={p.x} cy={p.y} r={3} fill="#a855f7" />
            ))}

            {/* X-axis labels (first and last) */}
            {[chart.points[0], chart.points[chart.points.length - 1]].map((p, i) => (
              <text
                key={i}
                x={p.x}
                y={CHART_H - 6}
                textAnchor={i === 0 ? 'start' : 'end'}
                fontSize={9}
                fill="#9ca3af"
              >
                {format(new Date(p.s.measuredAt), 'MMM d')}
              </text>
            ))}
          </svg>
        </div>
      )}

      {/* Latest FTP callout */}
      {snapshots.length > 0 && (() => {
        const last = [...snapshots].reverse().find((s) => s.ftp != null)
        if (!last) return null
        const trend = (() => {
          const withFTP = snapshots.filter((s) => s.ftp != null)
          if (withFTP.length < 2) return null
          const prev = withFTP[withFTP.length - 2].ftp as number
          const curr = withFTP[withFTP.length - 1].ftp as number
          const delta = curr - prev
          return { delta, pct: ((delta / prev) * 100).toFixed(1) }
        })()
        return (
          <div className="mt-2 flex items-center gap-3">
            <div>
              <p className="text-xs text-gray-500">Latest FTP</p>
              <p className="text-lg font-bold text-purple-700">{last.ftp} <span className="text-xs font-normal text-gray-400">W</span></p>
            </div>
            {trend && (
              <div className={`text-xs font-semibold px-2 py-0.5 rounded-full ${trend.delta >= 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
                {trend.delta >= 0 ? '+' : ''}{trend.delta}W ({trend.delta >= 0 ? '+' : ''}{trend.pct}%)
              </div>
            )}
          </div>
        )
      })()}
    </div>
  )
}
