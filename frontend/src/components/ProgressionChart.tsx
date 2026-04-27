import { useEffect, useRef, useState } from 'react'
import { TrendingUp, RefreshCw } from 'lucide-react'
import { format } from 'date-fns'
import { useShallow } from 'zustand/shallow'
import { useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import type { AthleteMetricSnapshot } from '../store/useAppStore'
import { triggerStravaHistoryImport, getStravaImportProgress } from '../services/strava'
import { LineChart } from './charts/LineChart'
import { useMetricsPipeline } from '../hooks/useMetricsPipeline'
import { useImportProgress } from '../hooks/useImportProgress'

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export default function ProgressionChart() {
  const { metricsHistory, authToken, stravaConnection } = useAppStore(
    useShallow((s) => ({
      metricsHistory: s.metricsHistory,
      authToken: s.authToken,
      stravaConnection: s.stravaConnection,
    }))
  )

  const { recalculateAll } = useMetricsPipeline()
  const queryClient = useQueryClient()
  const importProgress = useImportProgress()

  type RecalcStatus = 'idle' | 'importing' | 'recalculating' | 'done' | 'error'
  const [recalcStatus, setRecalcStatus] = useState<RecalcStatus>('idle')
  const [recalcError, setRecalcError] = useState('')
  const unmountedRef = useRef(false)
  const idleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isImportRunning = !!stravaConnection && importProgress.status === 'running'

  useEffect(() => {
    return () => {
      unmountedRef.current = true
      if (idleTimeoutRef.current !== null) {
        clearTimeout(idleTimeoutRef.current)
        idleTimeoutRef.current = null
      }
    }
  }, [])

  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

  const handleRecalculate = async () => {
    if (!authToken || isImportRunning || recalcStatus === 'importing' || recalcStatus === 'recalculating') return
    setRecalcStatus('idle')
    setRecalcError('')

    try {
      if (stravaConnection) {
        // Step 1: kick off a full Strava history import (up to 24 months)
        setRecalcStatus('importing')
        await triggerStravaHistoryImport(authToken, 24, true)

        // Step 2: wait until the background import finishes.
        while (!unmountedRef.current) {
          const progress = await getStravaImportProgress(authToken)
          if (progress.status === 'done') break
          if (progress.status === 'error') {
            throw new Error(progress.error || 'Import failed')
          }
          await sleep(2000)
        }
        if (unmountedRef.current) return
      }

      // Step 3: rebuild CTL/ATL/TSB per-ride snapshots and refresh store
      setRecalcStatus('recalculating')
      await recalculateAll()
      if (unmountedRef.current) return
      // Invalidate the readiness score so RaceReadinessCard re-fetches with fresh data
      queryClient.invalidateQueries({ queryKey: ['readiness-score'] })
      setRecalcStatus('done')
      idleTimeoutRef.current = setTimeout(() => {
        if (!unmountedRef.current) setRecalcStatus('idle')
      }, 3000)
    } catch (e) {
      if (unmountedRef.current) return
      setRecalcError(e instanceof Error ? e.message : 'Recalculation failed')
      setRecalcStatus('error')
    }
  }

  const recalcLabel = () => {
    if (isImportRunning || recalcStatus === 'importing') {
      return importProgress.total > 0
        ? `Downloading… ${importProgress.processed}/${importProgress.total}`
        : 'Downloading rides…'
    }
    if (recalcStatus === 'recalculating') return 'Recalculating…'
    if (recalcStatus === 'done') return 'Done!'
    return 'Recalculate'
  }

  const isRecalcBusy = isImportRunning || recalcStatus === 'importing' || recalcStatus === 'recalculating'

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
        <div className="flex flex-col items-center gap-1">
          <button
            onClick={handleRecalculate}
            disabled={isRecalcBusy}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <RefreshCw size={12} className={isRecalcBusy ? 'animate-spin' : ''} />
            {recalcLabel()}
          </button>
          {recalcStatus === 'error' && (
            <p className="text-xs text-red-500">{recalcError}</p>
          )}
        </div>
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

      {/* Recalculate button */}
      <div className="flex flex-col items-start gap-1 pt-1">
        <button
          onClick={handleRecalculate}
          disabled={isRecalcBusy}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <RefreshCw size={12} className={isRecalcBusy ? 'animate-spin' : ''} />
          {recalcLabel()}
        </button>
        {recalcStatus === 'error' && (
          <p className="text-xs text-red-500">{recalcError}</p>
        )}
      </div>
    </div>
  )
}
