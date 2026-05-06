import type { AthleteMetricSnapshot } from '../store/useAppStore'

interface Props {
  metricsHistory: AthleteMetricSnapshot[]
}

export default function TrainingStatusBadge({ metricsHistory }: Props) {
  if (metricsHistory.length < 7) return null

  const latest = metricsHistory[metricsHistory.length - 1]
  const tsb = latest.tsb ?? 0

  let label: string
  let colorClass: string
  if (tsb > 10) {
    label = `Fresh · TSB +${Math.round(tsb)}`
    colorClass = 'bg-green-100 text-green-700'
  } else if (tsb > -5) {
    label = `Building · TSB ${Math.round(tsb)}`
    colorClass = 'bg-blue-100 text-blue-700'
  } else if (tsb > -20) {
    label = `Loaded · TSB ${Math.round(tsb)}`
    colorClass = 'bg-amber-100 text-amber-700'
  } else {
    label = `Overreaching · TSB ${Math.round(tsb)}`
    colorClass = 'bg-red-100 text-red-700'
  }

  return (
    <div className="flex items-center gap-2">
      <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold ${colorClass}`}>
        {label}
      </span>
      {latest.ctl !== undefined && (
        <span className="text-xs text-gray-400">
          Fitness {Math.round(latest.ctl)}
          {latest.atl !== undefined ? ` · Fatigue ${Math.round(latest.atl)}` : ''}
        </span>
      )}
    </div>
  )
}
