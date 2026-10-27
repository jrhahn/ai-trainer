import { useT } from '../i18n'
import { useAppStore } from '../store/useAppStore'
import type { RideMetricPoint } from '../store/useAppStore'
import { rideLoad } from '../utils/rideLoad'

// A ride's load, in expert mode only (ai-trainer-ops#311, the owner's "Weg 1"):
// the number the coach plans with, an estimate marked "≈" and said where it came
// from. Text in the row's own grey, no icon and no badge (owner's review,
// 2026-10-23). Everyone else sees no number; the load in words for everyone is
// a later step.

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

/** On the day: "Load ≈ 45 · estimated from your heart rate", or "Load 85". */
export function RideLoadLine({ ride }: { ride: RideMetricPoint }) {
  const t = useT()
  const isExpertMode = useAppStore((s) => s.isExpertMode)
  const load = rideLoad(ride)
  if (!isExpertMode || !load) return null
  return (
    <p className="text-gray-500">
      {load.estimatedFrom
        ? `${t('Load ≈ {value}', { value: load.value })} · ${t(load.estimatedFrom)}`
        : t('Load {value}', { value: load.value })}
    </p>
  )
}
