import { useT } from '../i18n'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { rideLoad, rideLoadLevel, type LoadLevel } from '../utils/rideLoad'

// A ride's load, in expert mode only (ai-trainer-ops#311, the owner's "Weg 1"):
// the number the coach plans with, an estimate marked "≈" and said where it came
// from. Text in the row's own grey, no icon and no badge (owner's review,
// 2026-10-23). On the day, everyone also sees the load in a word measured
// against their own recent rides (ai-trainer-ops#313, "Weg 2"); the number
// stays expert mode's.

const LEVEL_TEXT: Record<LoadLevel, string> = {
  easy: 'light',
  moderate: 'moderate',
  hard: 'high',
}

/** In a list of rides: "Load 85", or "Load ≈ 45" with how it was estimated on hover. */
export function RideLoadInRow({ ride }: { ride: RideMetricPoint }) {
  const t = useT()
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const load = rideLoad(ride)
  if (!isExpertMode || !load) return null
  if (!load.estimatedFrom) {
    return <span className="shrink-0 text-gray-500">{t('Load {value}', { value: load.value })}</span>
  }
  return (
    <span className="shrink-0 text-gray-500" title={t('Load {how}', { how: t(load.estimatedFrom) })}>
      {t('Load ≈ {value}', { value: load.value })}
    </span>
  )
}

/** On the day: "Load: high (estimated from your heart rate)" for everyone once
 *  there are enough rides to compare with; expert mode adds the number,
 *  "Load: high (≈ 90, estimated from your heart rate)". */
export function RideLoadLine({ ride }: { ride: RideMetricPoint }) {
  const t = useT()
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const history = useAppStore((s) => s.rideMetricsHistory)
  const load = rideLoad(ride)
  if (!load) return null
  const level = rideLoadLevel(ride, history)
  if (level) {
    const head = t('Load: {level}', { level: t(LEVEL_TEXT[level]) })
    const details = [
      isExpertMode ? (load.estimatedFrom ? `≈ ${load.value}` : String(load.value)) : null,
      load.estimatedFrom ? t(load.estimatedFrom) : null,
    ].filter((detail): detail is string => detail != null)
    return (
      <p className="text-gray-500" title={t('Compared with your rides of the same sport in the last six weeks')}>
        {details.length > 0 ? `${head} (${details.join(', ')})` : head}
      </p>
    )
  }
  if (!isExpertMode) return null
  return (
    <p className="text-gray-500">
      {load.estimatedFrom
        ? `${t('Load ≈ {value}', { value: load.value })} · ${t(load.estimatedFrom)}`
        : t('Load {value}', { value: load.value })}
    </p>
  )
}
