export function formatActivityType(sportType: string): string {
  const normalized = sportType.trim()
  if (!normalized) return ''

  const withoutRideSuffix = normalized.replace(/Ride$/i, '')
  const baseType = withoutRideSuffix.trim() || normalized
  const collapsed = baseType.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
  const lower = collapsed.toLowerCase()

  return lower.charAt(0).toUpperCase() + lower.slice(1)
}
