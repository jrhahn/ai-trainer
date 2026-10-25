import { useAppStore } from '../store/useAppStore'
import { useT } from '../i18n'
import { useImportProgress } from '../hooks/useImportProgress'
import { card, cardTitle, note } from './ui'

/** How far the analysis of the imported history has got, while it runs.
 *  Out of `DashboardPage` (ai-trainer-ops#274). */
export default function ActivityAnalysisProgress() {
  const t = useT()
  const stravaConnection = useAppStore((s) => s.stravaConnection)
  const importProgress = useImportProgress()

  const analyzedActivities = Math.min(importProgress.processed, importProgress.total)
  const hasActivityProgress = !!stravaConnection && importProgress.status !== 'idle' && importProgress.total > 0
  if (!hasActivityProgress) return null
  const progressPct = Math.round((analyzedActivities / importProgress.total) * 100)

  return (
    <div className={card}>
      <p className={`${cardTitle} mb-2`}>{t('Activity analysis')}</p>
      <div className="h-1.5 w-full rounded-full bg-gray-100">
        <div
          className="h-1.5 rounded-full bg-amber-400 transition-all duration-300"
          style={{ width: `${progressPct}%` }}
        />
      </div>
      <p className={`mt-2 ${note(true)}`}>
        {t('{done} of {total} activities analysed', { done: analyzedActivities, total: importProgress.total })}
      </p>
      {importProgress.status === 'error' && importProgress.error && (
        <p className={`mt-1 ${note(false)}`}>{importProgress.error}</p>
      )}
    </div>
  )
}
