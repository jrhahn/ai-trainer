/**
 * FitFileUpload
 *
 * Lets the user upload one or more .fit files (Garmin / Wahoo / Zwift export)
 * without needing Strava OAuth. Results are shown per file.
 */

import { useRef, useState } from 'react'
import { Upload, CheckCircle, AlertCircle, CircleSlash, FileWarning } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { uploadFitFiles } from '../services/user'
import type { FitBulkUploadResponse, FitUploadFileResult } from '../services/user'

export default function FitFileUpload() {
  const authToken = useAppStore((s) => s.authToken)
  const inputRef = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<FitBulkUploadResponse | null>(null)
  const [error, setError] = useState('')

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (!files.length || !authToken) return
    setStatus('uploading')
    setError('')
    setResult(null)
    try {
      const res = await uploadFitFiles(authToken, files)
      setResult(res)
      setStatus('success')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setStatus('error')
    } finally {
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Upload size={16} className="text-blue-500" />
        <h3 className="text-sm font-bold text-gray-800">Upload .fit Files</h3>
        <span className="ml-auto text-xs text-gray-400">Garmin · Wahoo · Zwift</span>
      </div>

      <p className="text-xs text-gray-500 mb-3">
        Import workouts directly from .fit files — no Strava account required.
        Supports cycling, running, and other sports.
      </p>

      <label className={`
        flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-lg border-2 border-dashed cursor-pointer
        text-sm font-medium transition-colors
        ${status === 'uploading'
          ? 'border-blue-200 text-blue-400 bg-blue-50 cursor-not-allowed'
          : 'border-gray-200 text-gray-500 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50'}
      `}>
        <Upload size={14} />
        {status === 'uploading' ? 'Uploading…' : 'Choose .fit files'}
        <input
          ref={inputRef}
          type="file"
          accept=".fit"
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
            <span>{result.imported} imported</span>
            <span className="text-gray-300">·</span>
            <span>{result.skipped} skipped</span>
            <span className="text-gray-300">·</span>
            <span>{result.failed} failed</span>
          </div>
          <div className="space-y-1">
            {result.files.map((file, index) => (
              <FitUploadResultRow key={`${file.filename}-${file.status}-${index}`} file={file} />
            ))}
          </div>
        </div>
      )}

      {status === 'error' && (
        <div className="mt-3 flex items-start gap-2 text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
          <p className="text-xs">{error}</p>
        </div>
      )}
    </div>
  )
}

function FitUploadResultRow({ file }: { file: FitUploadFileResult }) {
  const icon =
    file.status === 'imported' ? (
      <CheckCircle size={13} className="mt-0.5 flex-shrink-0 text-green-600" />
    ) : file.status === 'skipped' ? (
      <CircleSlash size={13} className="mt-0.5 flex-shrink-0 text-amber-600" />
    ) : (
      <FileWarning size={13} className="mt-0.5 flex-shrink-0 text-red-600" />
    )
  const detail = [
    file.sportType ? `${file.sportType}` : null,
    file.durationMinutes ? `${file.durationMinutes} min` : null,
    file.averagePower ? `${file.averagePower}W` : null,
    file.averageHeartRate ? `${file.averageHeartRate} bpm` : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className="flex items-start gap-2 rounded-md bg-white px-2 py-1.5 text-xs text-gray-700">
      {icon}
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-gray-800">{file.filename}</p>
        <p className="text-gray-500">{detail || file.message}</p>
      </div>
    </div>
  )
}
