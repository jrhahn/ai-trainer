import { TrendingUp } from 'lucide-react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { AthleteMetricSnapshot } from '../store/useAppStore'

// ---------------------------------------------------------------------------
// Tiny reusable SVG line-chart
// ---------------------------------------------------------------------------

interface LineChartProps {
  data: number[]
  labels: string[]
  color: string
  /** optional second series rendered as a dashed line */
  data2?: number[]
  color2?: string
  /** optional third series */
  data3?: number[]
  color3?: string
  height?: number
  yLabel?: string
}

function LineChart({
  data,
  labels,
  color,
  data2,
  color2,
  data3,
  color3,
  height = 80,
  yLabel,
}: LineChartProps) {
  const width = 400
  const padX = 8
  const padY = 10

  const allValues = [
    ...data,
    ...(data2 ?? []),
    ...(data3 ?? []),
  ].filter((v) => isFinite(v))

  if (allValues.length === 0) return null

  const minVal = Math.min(...allValues)
  const maxVal = Math.max(...allValues)
  const range = maxVal - minVal || 1

  const toX = (i: number, total: number) =>
    padX + (i / Math.max(total - 1, 1)) * (width - padX * 2)
  const toY = (v: number) =>
    padY + ((maxVal - v) / range) * (height - padY * 2)

  const polyline = (values: number[]) =>
    values.map((v, i) => `${toX(i, values.length)},${toY(v)}`).join(' ')

  return (
    <div className="w-full overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ minWidth: '240px' }}
        aria-label={yLabel ?? 'Line chart'}
      >
        {/* Horizontal grid lines */}
        {[0, 0.5, 1].map((t) => {
          const y = padY + t * (height - padY * 2)
          const val = maxVal - t * range
          return (
            <g key={t}>
              <line
                x1={padX}
                y1={y}
                x2={width - padX}
                y2={y}
                stroke="#e5e7eb"
                strokeWidth="0.5"
              />
              <text
                x={padX}
                y={y - 2}
                fontSize="6"
                fill="#9ca3af"
              >
                {Math.round(val)}
              </text>
            </g>
          )
        })}

        {/* Series 1 */}
        <polyline
          points={polyline(data)}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((v, i) => (
          <circle
            key={i}
            cx={toX(i, data.length)}
            cy={toY(v)}
            r="2"
            fill={color}
          />
        ))}

        {/* Series 2 (dashed) */}
        {data2 && color2 && (
          <>
            <polyline
              points={polyline(data2)}
              fill="none"
              stroke={color2}
              strokeWidth="1.5"
              strokeDasharray="3 2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {data2.map((v, i) => (
              <circle key={i} cx={toX(i, data2.length)} cy={toY(v)} r="1.5" fill={color2} />
            ))}
          </>
        )}

        {/* Series 3 (dotted) */}
        {data3 && color3 && (
          <>
            <polyline
              points={polyline(data3)}
              fill="none"
              stroke={color3}
              strokeWidth="1.5"
              strokeDasharray="1.5 3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {data3.map((v, i) => (
              <circle key={i} cx={toX(i, data3.length)} cy={toY(v)} r="1.5" fill={color3} />
            ))}
          </>
        )}

        {/* X-axis labels — show first, middle, last */}
        {[0, Math.floor((labels.length - 1) / 2), labels.length - 1]
          .filter((i, pos, arr) => arr.indexOf(i) === pos && i < labels.length)
          .map((i) => (
            <text
              key={i}
              x={toX(i, labels.length)}
              y={height - 1}
              fontSize="6"
              fill="#9ca3af"
              textAnchor="middle"
            >
              {labels[i]}
            </text>
          ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export default function ProgressionChart() {
  const metricsHistory = useAppStore(useShallow((s) => s.metricsHistory))

  if (metricsHistory.length < 2) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center">
            <TrendingUp size={16} className="text-purple-600" />
          </div>
          <h3 className="text-sm font-bold text-gray-800">Athlete Progression</h3>
        </div>
        <p className="text-xs text-gray-400 text-center py-4">
          Come back after your next analysis to see your fitness progression chart.
        </p>
      </div>
    )
  }

  const snapshots: AthleteMetricSnapshot[] = metricsHistory

  // CTL / ATL / TSB — the canonical "full riding history" timeline
  const ctlSnapshots = snapshots.filter((s) => s.ctl != null)
  const ctlData = ctlSnapshots.map((s) => s.ctl as number)
  const atlData = ctlSnapshots.map((s) => s.atl as number)
  const tsbData = ctlSnapshots.map((s) => s.tsb as number)
  const loadLabels = ctlSnapshots.map((s) => {
    try {
      return format(new Date(s.recordedAt), 'MMM d')
    } catch {
      return ''
    }
  })

  // FTP — forward-fill the last known FTP across the full CTL timeline so the
  // chart spans the entire riding history (same x-axis as CTL / ATL).
  let runningFtp: number | null = null
  let firstFtpIdx = -1
  const ftpAligned = ctlSnapshots.map((s, i) => {
    if (s.ftp != null) {
      if (firstFtpIdx === -1) firstFtpIdx = i
      runningFtp = s.ftp
    }
    return runningFtp
  })
  const ftpData = firstFtpIdx >= 0 ? (ftpAligned.slice(firstFtpIdx) as number[]) : []
  const ftpLabels = firstFtpIdx >= 0 ? loadLabels.slice(firstFtpIdx) : []

  const thrHrData = snapshots
    .filter((s) => s.thresholdHR != null)
    .map((s) => s.thresholdHR as number)
  const thrHrLabels = snapshots
    .filter((s) => s.thresholdHR != null)
    .map((s) => {
      try {
        return format(new Date(s.recordedAt), 'MMM d')
      } catch {
        return ''
      }
    })

  const latestFTP = snapshots.findLast((s) => s.ftp != null)?.ftp
  const latestCTL = snapshots.findLast((s) => s.ctl != null)?.ctl
  const latestATL = snapshots.findLast((s) => s.atl != null)?.atl
  const latestTSB = snapshots.findLast((s) => s.tsb != null)?.tsb

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center">
          <TrendingUp size={16} className="text-purple-600" />
        </div>
        <h3 className="text-sm font-bold text-gray-800">Athlete Progression</h3>
        <span className="ml-auto text-xs text-gray-400">
          {snapshots.length} data point{snapshots.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Summary badges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {latestFTP != null && (
          <div className="bg-purple-50 rounded-lg px-3 py-2 text-center">
            <p className="text-xs text-purple-500 font-medium">FTP</p>
            <p className="text-base font-bold text-purple-800">{latestFTP}<span className="text-xs font-normal">W</span></p>
          </div>
        )}
        {latestCTL != null && (
          <div className="bg-blue-50 rounded-lg px-3 py-2 text-center">
            <p className="text-xs text-blue-500 font-medium">Fitness (CTL)</p>
            <p className="text-base font-bold text-blue-800">{latestCTL.toFixed(1)}</p>
          </div>
        )}
        {latestATL != null && (
          <div className="bg-orange-50 rounded-lg px-3 py-2 text-center">
            <p className="text-xs text-orange-500 font-medium">Fatigue (ATL)</p>
            <p className="text-base font-bold text-orange-800">{latestATL.toFixed(1)}</p>
          </div>
        )}
        {latestTSB != null && (
          <div
            className={`rounded-lg px-3 py-2 text-center ${
              latestTSB >= 0 ? 'bg-green-50' : 'bg-red-50'
            }`}
          >
            <p
              className={`text-xs font-medium ${
                latestTSB >= 0 ? 'text-green-500' : 'text-red-500'
              }`}
            >
              Form (TSB)
            </p>
            <p
              className={`text-base font-bold ${
                latestTSB >= 0 ? 'text-green-800' : 'text-red-800'
              }`}
            >
              {latestTSB > 0 ? '+' : ''}{latestTSB.toFixed(1)}
            </p>
          </div>
        )}
      </div>

      {/* FTP chart */}
      {ftpData.length >= 2 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">⚡ FTP History (W)</p>
          <LineChart
            data={ftpData}
            labels={ftpLabels}
            color="#9333ea"
            height={72}
            yLabel="FTP in Watts"
          />
        </div>
      )}

      {/* Threshold HR chart */}
      {thrHrData.length >= 2 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">❤️ Threshold HR History (bpm)</p>
          <LineChart
            data={thrHrData}
            labels={thrHrLabels}
            color="#ef4444"
            height={72}
            yLabel="Threshold HR in bpm"
          />
        </div>
      )}

      {/* CTL / ATL / TSB chart */}
      {ctlData.length >= 2 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">📊 Training Load (CTL / ATL / TSB)</p>
          <div className="flex items-center gap-4 mb-1">
            <span className="flex items-center gap-1 text-xs text-blue-600">
              <span className="inline-block w-4 h-0.5 bg-blue-500" /> CTL
            </span>
            <span className="flex items-center gap-1 text-xs text-orange-500">
              <span className="inline-block w-4 border-t border-dashed border-orange-400" /> ATL
            </span>
            {tsbData.length >= 2 && (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <span className="inline-block w-4 border-t border-dotted border-green-500" /> TSB
              </span>
            )}
          </div>
          <LineChart
            data={ctlData}
            labels={loadLabels}
            color="#3b82f6"
            data2={atlData.length >= 2 ? atlData : undefined}
            color2={atlData.length >= 2 ? '#f97316' : undefined}
            data3={tsbData.length >= 2 ? tsbData : undefined}
            color3={tsbData.length >= 2 ? '#22c55e' : undefined}
            height={80}
            yLabel="Training stress score"
          />
        </div>
      )}

      <p className="text-xs text-gray-400">
        Updated after each Strava ride analysis. CTL = fitness (42-day avg), ATL = fatigue (7-day avg), TSB = form (CTL − ATL).
      </p>
    </div>
  )
}
