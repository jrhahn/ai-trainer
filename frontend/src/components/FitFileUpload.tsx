/**
 * FitFileUpload
 *
 * Lets the user upload one or more .fit files (Garmin / Wahoo / Zwift export)
 * without needing Strava OAuth. Results are shown per file.
 */

import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Upload, CheckCircle, AlertCircle, CircleSlash, FileWarning } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { fetchMetricsHistory, fetchRideMetricsHistory, uploadFitFiles } from '../services/user'
import type { FitBulkUploadResponse, FitUploadFileResult } from '../services/user'
import { useT } from '../i18n'

interface FitFileUploadProps {
  embedded?: boolean
}

/** Files per request: the backend's `fit_upload_bulk_max_files`. A larger
 * selection, a few months of rides say, goes up in parts of this size
 * (ai-trainer-ops#157) instead of being refused whole with a 413. */
export const MAX_FILES_PER_UPLOAD = 25

function merge(parts: FitBulkUploadResponse[]): FitBulkUploadResponse {
  const failed = parts.reduce((n, p) => n + p.failed, 0)
  return {
    status: failed === 0 ? 'ok' : 'partial',
    total: parts.reduce((n, p) => n + p.total, 0),
    imported: parts.reduce((n, p) => n + p.imported, 0),
    skipped: parts.reduce((n, p) => n + p.skipped, 0),
    failed,
    files: parts.flatMap((p) => p.files),
  }
}

export default function FitFileUpload({ embedded = false }: FitFileUploadProps) {
  const authToken = useAppStore((s) => s.authToken)
  const setRideMetricsHistory = useAppStore((s) => s.setRideMetricsHistory)
  const setMetricsHistory = useAppStore((s) => s.setMetricsHistory)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<FitBulkUploadResponse | null>(null)
  const [error, setError] = useState('')
  const [sent, setSent] = useState({ done: 0, total: 0 })
  const t = useT()

  // What was imported is on the dashboard now, not after the next reload
  // (ai-trainer-ops#48). Best-effort: the import itself already succeeded.
  const refresh = async (token: string) => {
    const [rides, metrics] = await Promise.all([
      fetchRideMetricsHistory(token),
      fetchMetricsHistory(token),
    ]).catch(() => [null, null] as const)
    if (rides) setRideMetricsHistory(rides)
    if (metrics) setMetricsHistory(metrics)
  }

  const uploadFiles = async (files: File[]) => {
    if (!files.length || !authToken || status === 'uploading') return
    setStatus('uploading')
    setError('')
    setResult(null)
    const parts: FitBulkUploadResponse[] = []
    try {
      for (let start = 0; start < files.length; start += MAX_FILES_PER_UPLOAD) {
        setSent({ done: start, total: files.length })
        parts.push(await uploadFitFiles(authToken, files.slice(start, start + MAX_FILES_PER_UPLOAD)))
      }
      const res = merge(parts)
      setResult(res)
      setStatus('success')
      if (res.imported > 0) await refresh(authToken)
    } catch (err) {
      // What earlier parts imported stays imported; say so beside the error,
      // and show it on the dashboard too.
      if (parts.length) {
        const sofar = merge(parts)
        setResult(sofar)
        if (sofar.imported > 0) await refresh(authToken)
      }
      setError(err instanceof Error ? err.message : 'Upload failed')
      setStatus('error')
    } finally {
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) =>
    uploadFiles(Array.from(e.target.files ?? []))

  return (
    <div className={embedded ? '' : 'bg-white rounded-xl shadow-xs border border-gray-100 p-4'}>
      <div className="flex items-center gap-2 mb-3">
        <Upload size={16} className="text-blue-500" />
        <h3 className="text-sm font-bold text-gray-800">{t('Upload files')}</h3>
        <span className="ml-auto text-xs text-gray-400">Garmin · Wahoo · Zwift</span>
      </div>

      <p className="text-xs text-gray-500 mb-3">
        {t('A .fit file from your device, or the .zip Garmin Connect gives you with “Export Original”. Cycling, running and other sports.')}
      </p>

      <label
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          void uploadFiles(Array.from(e.dataTransfer.files))
        }}
        className={`
        flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-lg border-2 border-dashed cursor-pointer
        text-sm font-medium transition-colors
        ${status === 'uploading'
          ? 'border-blue-200 text-blue-400 bg-blue-50 cursor-not-allowed'
          : dragging
            ? 'border-blue-400 text-blue-600 bg-blue-50'
            : 'border-gray-200 text-gray-500 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50'}
      `}>
        <Upload size={14} />
        {status === 'uploading'
          ? sent.total > MAX_FILES_PER_UPLOAD
            ? t('Uploading… {done} of {total} files', { done: sent.done, total: sent.total })
            : t('Uploading…')
          : t('Choose or drop .fit / .zip files')}
        <input
          ref={inputRef}
          type="file"
          accept=".fit,.zip,.gpx,.tcx"
          multiple
          className="sr-only"
          disabled={status === 'uploading'}
          onChange={handleFileChange}
        />
      </label>

      {status === 'success' && result && (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-gray-700">
            <CheckCircle size={14} className="text-green-600" />
            <span>{t('{n} imported', { n: result.imported })}</span>
            <span className="text-gray-300">·</span>
            <span>{t('{n} skipped', { n: result.skipped })}</span>
            <span className="text-gray-300">·</span>
            <span>{t('{n} failed', { n: result.failed })}</span>
          </div>
          <div className="space-y-1">
            {result.files.map((file, index) => (
              <FitUploadResultRow key={`${file.filename}-${file.status}-${index}`} file={file} />
            ))}
          </div>
        </div>
      )}

      {status === 'error' && result && result.imported > 0 && (
        <p className="mt-3 text-xs text-gray-600">
          {t('{n} imported before the error.', { n: result.imported })}
        </p>
      )}

      {status === 'error' && (
        <div className="mt-3 flex items-start gap-2 text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <p className="text-xs">{t(error)}</p>
        </div>
      )}
    </div>
  )
}

function FitUploadResultRow({ file }: { file: FitUploadFileResult }) {
  const t = useT()
  const icon =
    file.status === 'imported' ? (
      <CheckCircle size={13} className="mt-0.5 shrink-0 text-green-600" />
    ) : file.status === 'skipped' ? (
      <CircleSlash size={13} className="mt-0.5 shrink-0 text-amber-600" />
    ) : (
      <FileWarning size={13} className="mt-0.5 shrink-0 text-red-600" />
    )
  const detail = [
    file.sportType ? `${file.sportType}` : null,
    file.durationMinutes ? `${file.durationMinutes} min` : null,
    file.averagePower ? `${file.averagePower}W` : null,
    file.averageHeartRate ? t('{n} bpm', { n: file.averageHeartRate }) : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className="flex items-start gap-2 rounded-md bg-white px-2 py-1.5 text-xs text-gray-700">
      {icon}
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-gray-800">{file.filename}</p>
        <p className="text-gray-500">{detail || t(file.message)}</p>
      </div>
      {file.status !== 'failed' && file.activityDate && (
        <Link
          to={`/workout/${file.activityDate}`}
          className="shrink-0 font-semibold text-blue-600 hover:underline"
        >
          {t('Open day')}
        </Link>
      )}
    </div>
  )
}
