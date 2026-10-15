export function formatActivityType(sportType: string): string {
  const normalized = sportType.trim()
  if (!normalized) return ''

  const withoutRideSuffix = normalized.replace(/Ride$/i, '')
  const baseType = withoutRideSuffix.trim() || normalized
  const collapsed = baseType.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
  const lower = collapsed.toLowerCase()

  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

/** A sport type as the activity lists print it: 'VirtualRide' → 'virtualride'.
 * Also the dictionary key, so a type nobody translated shows as it always did. */
export function sportTypeText(sportType: string): string {
  return sportType.toLowerCase().replace(/_/g, ' ')
}

export function activityNoun(sportType?: string | null): string {
  const normalized = (sportType ?? '').trim().toLowerCase()
  if (!normalized) return 'activity'
  if (normalized.includes('run')) return 'run'
  if (normalized.includes('hike')) return 'hike'
  if (normalized.includes('walk')) return 'walk'
  if (normalized.includes('weight') || normalized.includes('strength')) return 'strength session'
  if (normalized.includes('ride') || normalized === 'cycling') return 'ride'
  return 'activity'
}

export function isCyclingActivity(sportType?: string | null): boolean {
  const normalized = (sportType ?? '').trim().toLowerCase()
  return normalized === 'cycling' || normalized.includes('ride')
}
