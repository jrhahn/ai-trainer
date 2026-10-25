import { apiFetch } from './api'

export interface PowerCurve {
  durationSeconds: number
  points: { seconds: number; watts: number }[]
  model: {
    cp: number
    cpLow: number
    cpHigh: number
    wPrime: number
    wPrimeLow: number
    wPrimeHigh: number
    pointsUsed: number
  } | null
  curve: { seconds: number; watts: number; low: number; high: number }[]
}

/** The public power-curve tool. No token: it works without an account. */
export async function fetchPowerCurve(file: File): Promise<PowerCurve> {
  const body = new FormData()
  body.append('file', file)
  return apiFetch<PowerCurve>('/tools/power-curve', { method: 'POST', body })
}
