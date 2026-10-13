import { API_BASE } from './api'

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

/** The public power-curve tool. No credentials: it works without an account. */
export async function fetchPowerCurve(file: File): Promise<PowerCurve> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(`${API_BASE}/tools/power-curve`, { method: 'POST', body })
  if (!response.ok) {
    let message = 'Could not read this file'
    try {
      const data = await response.json() as { detail?: string }
      if (data.detail) message = data.detail
    } catch {
      // keep the generic message
    }
    throw new Error(message)
  }
  return response.json() as Promise<PowerCurve>
}
