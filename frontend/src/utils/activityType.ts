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
