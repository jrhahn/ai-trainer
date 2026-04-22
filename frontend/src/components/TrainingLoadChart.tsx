import { Activity } from 'lucide-react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'

// ---------------------------------------------------------------------------
// Tiny reusable SVG line-chart (private to this module)
// ---------------------------------------------------------------------------

interface LineChartProps {
  data: number[]
  labels: string[]
  color: string
  height?: number
  yLabel?: string
  zeroLine?: boolean
}

function LineChart({ data, labels, color, height = 80, yLabel, zeroLine = false }: LineChartProps) {
  const width = 400
  const padX = 8
  const padY = 10

  const finite = data.filter((v) => isFinite(v))
  if (finite.length === 0) return null

  const minVal = Math.min(...finite)
  const maxVal = Math.max(...finite)
  const range = maxVal - minVal || 1

  const toX = (i: number, total: number) =>
    padX + (i / Math.max(total - 1, 1)) * (width - padX * 2)
  const toY = (v: number) => padY + ((maxVal - v) / range) * (height - padY * 2)

  const polyline = (values: number[]) =>
    values.map((v, i) => `${toX(i, values.length)},${toY(v)}`).join(' ')

  const zeroY = zeroLine && minVal < 0 && maxVal > 0 ? toY(0) : null

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
              <text x={padX} y={y - 2} fontSize="6" fill="#9ca3af">
                {Math.round(val)}
              </text>
            </g>
          )
        })}

        {/* Zero reference line for TSB */}
        {zeroY !== null && (
          <line
            x1={padX}
            y1={zeroY}
            x2={width - padX}
            y2={zeroY}
            stroke="#6b7280"
            strokeWidth="0.8"
            strokeDasharray="4 2"
          />
        )}

        {/* Series line */}
        <polyline
          points={polyline(data)}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((v, i) => (
          <circle key={i} cx={toX(i, data.length)} cy={toY(v)} r="2" fill={color} />
        ))}

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

export default function TrainingLoadChart() {
  const rideMetricsHistory = useAppStore(useShallow((s) => s.rideMetricsHistory))

  if (rideMetricsHistory.length < 2) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
            <Activity size={16} className="text-blue-600" />
          </div>
          <h3 className="text-sm font-bold text-gray-800">Training Load Over Time</h3>
        </div>
        <p className="text-xs text-gray-400 text-center py-4">
          Sync your Strava rides to see how your training load develops over time.
        </p>
      </div>
    )
  }

  const rides: RideMetricPoint[] = rideMetricsHistory

  const labels = rides.map((r) => {
    try {
      return format(new Date(r.activityDate), 'MMM d')
    } catch {
      return ''
    }
  })

  const ctlData = rides.map((r) => r.ctlAfter ?? 0)
  const atlData = rides.map((r) => r.atlAfter ?? 0)
  const tsbData = rides.map((r) => r.tsbAfter ?? 0)
  const tssData = rides.filter((r) => r.tss != null).map((r) => r.tss as number)
  const tssLabels = rides
    .filter((r) => r.tss != null)
    .map((r) => {
      try {
        return format(new Date(r.activityDate), 'MMM d')
      } catch {
        return ''
      }
    })

  const hasCtl = ctlData.some((v) => v > 0)
  const hasAtl = atlData.some((v) => v > 0)
  const hasTsb = tsbData.some((v) => v !== 0)
  const hasTss = tssData.length >= 2

  const latestCTL = [...ctlData].reverse().find((v) => v > 0)
  const latestATL = [...atlData].reverse().find((v) => v > 0)
  const latestTSB = tsbData[tsbData.length - 1]
  const latestTSS = tssData[tssData.length - 1]

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
          <Activity size={16} className="text-blue-600" />
        </div>
        <h3 className="text-sm font-bold text-gray-800">Training Load Over Time</h3>
        <span className="ml-auto text-xs text-gray-400">
          {rides.length} ride{rides.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Summary badges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {latestCTL != null && latestCTL > 0 && (
          <div className="bg-blue-50 rounded-lg px-3 py-2 text-center">
            <p className="text-xs text-blue-500 font-medium">Fitness (CTL)</p>
            <p className="text-base font-bold text-blue-800">{latestCTL.toFixed(1)}</p>
          </div>
        )}
        {latestATL != null && latestATL > 0 && (
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
              {latestTSB > 0 ? '+' : ''}
              {latestTSB.toFixed(1)}
            </p>
          </div>
        )}
        {latestTSS != null && (
          <div className="bg-gray-50 rounded-lg px-3 py-2 text-center">
            <p className="text-xs text-gray-500 font-medium">Last TSS</p>
            <p className="text-base font-bold text-gray-800">{Math.round(latestTSS)}</p>
          </div>
        )}
      </div>

      {/* CTL chart */}
      {hasCtl && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">
            🔵 Fitness — CTL (Chronic Training Load, 42-day avg)
          </p>
          <LineChart
            data={ctlData}
            labels={labels}
            color="#3b82f6"
            height={72}
            yLabel="CTL"
          />
        </div>
      )}

      {/* ATL chart */}
      {hasAtl && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">
            🟠 Fatigue — ATL (Acute Training Load, 7-day avg)
          </p>
          <LineChart
            data={atlData}
            labels={labels}
            color="#f97316"
            height={72}
            yLabel="ATL"
          />
        </div>
      )}

      {/* TSB chart */}
      {hasTsb && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">
            🟢 Form — TSB (Training Stress Balance = CTL − ATL)
          </p>
          <LineChart
            data={tsbData}
            labels={labels}
            color="#22c55e"
            height={72}
            yLabel="TSB"
            zeroLine
          />
        </div>
      )}

      {/* TSS chart */}
      {hasTss && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">⚡ Daily TSS (Training Stress Score per ride)</p>
          <LineChart
            data={tssData}
            labels={tssLabels}
            color="#8b5cf6"
            height={72}
            yLabel="TSS per ride"
          />
        </div>
      )}

      <p className="text-xs text-gray-400">
        One data point per ride. CTL = 42-day fitness, ATL = 7-day fatigue, TSB = form (positive = fresh, negative = fatigued).
      </p>
    </div>
  )
}
