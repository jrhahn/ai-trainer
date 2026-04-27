import { AlertCircle, CheckCircle } from 'lucide-react'
import type { ImportProgress } from '../services/strava'

interface StravaImportSummaryProps {
  progress: ImportProgress
  compact?: boolean
}

const FAILURE_LIMIT = 10

export default function StravaImportSummary({ progress, compact = false }: StravaImportSummaryProps) {
  const failures = progress.failedActivities ?? []
  const shownFailures = failures.slice(0, FAILURE_LIMIT)
  const hiddenFailures = Math.max(0, failures.length - shownFailures.length)

  return (
    <div className={compact ? 'space-y-2' : 'space-y-4 text-left'}>
      <div className={compact ? 'grid grid-cols-2 gap-2' : 'grid grid-cols-2 gap-3'}>
        <SummaryStat label="Found" value={progress.total} />
        <SummaryStat label="Processed" value={progress.processed} />
        <SummaryStat label="Imported" value={progress.imported} tone="success" />
        <SummaryStat label="Skipped" value={progress.skipped} tone={progress.skipped > 0 ? 'warn' : 'default'} />
      </div>

      {failures.length > 0 ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="flex items-center gap-2 text-amber-800">
            <AlertCircle size={15} />
            <p className="text-xs font-semibold">Some activities could not be imported</p>
          </div>
          <div className="mt-2 space-y-2">
            {shownFailures.map((failure, index) => (
              <div key={`${failure.activityId ?? 'unknown'}-${index}`} className="text-xs text-amber-900">
                <p className="font-medium">
                  {failure.activityName || 'Unnamed activity'}
                  {failure.activityDate ? ` - ${failure.activityDate}` : ''}
                  {failure.activityId ? ` - #${failure.activityId}` : ''}
                </p>
                <p className="text-amber-700">{failure.reason}</p>
              </div>
            ))}
            {hiddenFailures > 0 && (
              <p className="text-xs text-amber-700">
                {hiddenFailures} more skipped activit{hiddenFailures === 1 ? 'y' : 'ies'} not shown.
              </p>
            )}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-green-700">
          <CheckCircle size={15} />
          <p className="text-xs font-medium">All processed activities imported successfully.</p>
        </div>
      )}
    </div>
  )
}

function SummaryStat({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: number
  tone?: 'default' | 'success' | 'warn'
}) {
  const valueClass =
    tone === 'success' ? 'text-green-700' : tone === 'warn' ? 'text-amber-700' : 'text-gray-900'

  return (
    <div className="rounded-lg border border-gray-100 bg-white px-3 py-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</p>
      <p className={`text-lg font-bold ${valueClass}`}>{value}</p>
    </div>
  )
}
