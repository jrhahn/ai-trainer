import { Activity, Zap } from 'lucide-react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { LineChart } from './charts/LineChart'

/** One colour per series, for the line and for the key beside its heading.
 *
 * Hoisted out of the JSX because the headings used to be labelled with coloured
 * circle emoji — 🔵 against a `#3b82f6` line, 🟠 against `#f97316`
 * (ai-trainer-ops#51.7). Those are two unrelated colours that happen to have
 * similar names, set in two places, and the emoji one is whatever the reader's
 * font decides. Now the swatch cannot be a different colour from its line,
 * because it is the same string.
 */
const SERIES_COLORS = {
  ctl: '#3b82f6',
  atl: '#f97316',
  tsb: '#22c55e',
  tss: '#8b5cf6',
} as const

/** The key beside a chart's heading: a dot in the line's own colour.
 *
 * `aria-hidden`, and the heading says the series' name in words right after it
 * — the colour is a second channel, never the only one (WCAG 1.4.1).
 */
function SeriesKey({ color }: { color: string }) {
  return (
    <span
      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
      style={{ backgroundColor: color }}
      aria-hidden="true"
    />
  )
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export default function TrainingLoadChart() {
  const rideMetricsHistory = useAppStore(useShallow((s) => s.rideMetricsHistory))

  if (rideMetricsHistory.length < 2) {
    return (
      <div className="bg-white rounded-xl shadow-xs border border-gray-100 p-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
            <Activity size={16} className="text-blue-600" />
          </div>
          <h3 className="text-sm font-bold text-gray-800">Training Load Over Time</h3>
        </div>
        <p className="text-xs text-gray-400 text-center py-4">
          Sync your Strava activities to see how your training load develops over time.
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

  // Sessions without a power meter contribute an estimated load rather than a
  // zero (#579). The series is only honest if it says so when it holds one.
  const hasEstimatedLoad = rides.some(
    (r) => r.tss != null && r.tssSource != null && r.tssSource !== 'provider' && r.tssSource !== 'power',
  )

  const hasCtl = ctlData.some((v) => v > 0)
  const hasAtl = atlData.some((v) => v > 0)
  const hasTsb = tsbData.some((v) => v !== 0)
  const hasTss = tssData.length >= 2

  const latestCTL = [...ctlData].reverse().find((v) => v > 0)
  const latestATL = [...atlData].reverse().find((v) => v > 0)
  const latestTSB = tsbData[tsbData.length - 1]
  const latestTSS = tssData[tssData.length - 1]

  return (
    <div className="bg-white rounded-xl shadow-xs border border-gray-100 p-4 space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
          <Activity size={16} className="text-blue-600" />
        </div>
        <h3 className="text-sm font-bold text-gray-800">Training Load Over Time</h3>
        <span className="ml-auto text-xs text-gray-400">
          {rides.length} activit{rides.length === 1 ? 'y' : 'ies'}
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
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-gray-600">
            <SeriesKey color={SERIES_COLORS.ctl} />
            Fitness — CTL (Chronic Training Load, 42-day avg)
          </p>
          <LineChart
            data={ctlData}
            labels={labels}
            color={SERIES_COLORS.ctl}
            height={72}
            yLabel="CTL"
          />
        </div>
      )}

      {/* ATL chart */}
      {hasAtl && (
        <div>
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-gray-600">
            <SeriesKey color={SERIES_COLORS.atl} />
            Fatigue — ATL (Acute Training Load, 7-day avg)
          </p>
          <LineChart
            data={atlData}
            labels={labels}
            color={SERIES_COLORS.atl}
            height={72}
            yLabel="ATL"
          />
        </div>
      )}

      {/* TSB chart */}
      {hasTsb && (
        <div>
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-gray-600">
            <SeriesKey color={SERIES_COLORS.tsb} />
            Form — TSB (Training Stress Balance = CTL − ATL)
          </p>
          <LineChart
            data={tsbData}
            labels={labels}
            color={SERIES_COLORS.tsb}
            height={72}
            yLabel="TSB"
            zeroLine
          />
        </div>
      )}

      {/* TSS chart */}
      {hasTss && (
        <div>
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-gray-600">
            <Zap size={13} className="shrink-0" style={{ color: SERIES_COLORS.tss }} aria-hidden="true" />
            {hasEstimatedLoad
              ? 'Training load per activity (some estimated)'
              : 'Daily TSS (Training Stress Score per activity)'}
          </p>
          <LineChart
            data={tssData}
            labels={tssLabels}
            color={SERIES_COLORS.tss}
            height={72}
            yLabel={hasEstimatedLoad ? 'Load per activity' : 'TSS per activity'}
          />
          {hasEstimatedLoad && (
            <p className="text-xs text-gray-400 mt-1">
              Sessions without a power meter show an estimated load from heart rate or duration —
              real fatigue, but not a measured TSS.
            </p>
          )}
        </div>
      )}

      <p className="text-xs text-gray-400">
        One data point per activity. CTL = 42-day fitness, ATL = 7-day fatigue, TSB = form (positive = fresh, negative = fatigued).
      </p>
    </div>
  )
}
