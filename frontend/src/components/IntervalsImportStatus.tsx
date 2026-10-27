import { useT } from '../i18n'
import type { IntervalsImport } from '../hooks/useIntervalsImport'
import { note } from './ui'

/** How far an intervals.icu import is, and what came of it: the rides taken
 * in, and, after a connect, the coach's one look at them (ai-trainer-ops#306). */
export default function IntervalsImportStatus({
  intervalsImport,
}: {
  intervalsImport: Pick<IntervalsImport, 'progress' | 'importError'>
}) {
  const t = useT()
  const { progress, importError } = intervalsImport
  const ended = progress?.status === 'error' || progress?.status === 'interrupted'
  return (
    <>
      {progress?.status === 'running' && (
        <p role="status" className={note(true)}>
          {progress.total > 0
            ? t('Importing… {processed} of {total} activities', { processed: progress.processed, total: progress.total })
            : t('Importing…')}
        </p>
      )}
      {progress?.status === 'analysing' && (
        <p role="status" className={note(true)}>
          {t('{n} imported', { n: progress.imported })}. {t('Your coach is rebuilding your plan from them…')}
        </p>
      )}
      {progress?.status === 'done' && (
        <p role="status" className={note(true)}>
          {t('{n} imported', { n: progress.imported })}
          {progress.analysed &&
            ` · ${
              progress.planChanges
                ? t('Your coach rebuilt your plan from them. The chat says what changed.')
                : t('Your coach rebuilt your plan from them. It came out the same.')
            }`}
        </p>
      )}
      {(ended || importError) && (
        <p role="alert" className={note(false)}>
          {/* The server's sentences are fixed, so they translate (ai-trainer-ops#284). */}
          {importError || (progress?.error ? t(progress.error) : t('Import failed'))}
        </p>
      )}
    </>
  )
}
