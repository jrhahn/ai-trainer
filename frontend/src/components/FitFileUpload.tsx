/**
 * FitFileUpload
 *
 * Lets the user upload a .fit file (Garmin / Wahoo / Zwift export) without
 * needing Strava OAuth. The file is sent to POST /users/me/upload-fit and the
 * resulting summary is shown inline.
 */

import { useRef, useState } from 'react'
import { Upload, CheckCircle, AlertCircle } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { uploadFitFile } from '../services/user'
import { useQueryClient } from '@tanstack/react-query'

export default function FitFileUpload() {
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<{
    sportType: string
    durationMinutes: number
    averagePower?: number
    averageHeartRate?: number
  } | null>(null)
  const [error, setError] = useState('')

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !authToken) return
    setStatus('uploading')
    setError('')
    setResult(null)
    try {
      const res = await uploadFitFile(authToken, file)
      setResult({
        sportType: res.sportType,
        durationMinutes: res.durationMinutes,
        averagePower: res.averagePower,
        averageHeartRate: res.averageHeartRate,
      })
      setStatus('success')
      // Invalidate fitness history so the chart refreshes if analysis ran
      await queryClient.invalidateQueries({ queryKey: ['fitness-history'] })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setStatus('error')
    } finally {
      // Reset input so the same file can be re-selected
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Upload size={16} className="text-blue-500" />
        <h3 className="text-sm font-bold text-gray-800">Upload .fit File</h3>
        <span className="ml-auto text-xs text-gray-400">Garmin · Wahoo · Zwift</span>
      </div>

      <p className="text-xs text-gray-500 mb-3">
        Import a workout directly from a .fit file — no Strava account required.
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
        {status === 'uploading' ? 'Uploading…' : 'Choose .fit file'}
        <input
          ref={inputRef}
          type="file"
          accept=".fit"
          className="sr-only"
          disabled={status === 'uploading'}
          onChange={handleFileChange}
        />
      </label>

      {status === 'success' && result && (
        <div className="mt-3 flex items-start gap-2 text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <CheckCircle size={14} className="mt-0.5 flex-shrink-0" />
          <div className="text-xs">
            <p className="font-semibold capitalize">
              {result.sportType} · {result.durationMinutes} min
            </p>
            <p className="text-green-600">
              {[
                result.averagePower ? `${result.averagePower}W avg power` : null,
                result.averageHeartRate ? `${result.averageHeartRate} bpm avg HR` : null,
              ]
                .filter(Boolean)
                .join(' · ') || 'Activity saved'}
            </p>
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
