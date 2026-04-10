import type { StravaActivity } from '../store/useAppStore'
import { API_BASE, apiFetch } from './api'

export function getStravaAuthUrl(authToken: string): string {
  return `${API_BASE}/auth/strava?token=${encodeURIComponent(authToken)}`
}

export async function getStravaActivities(authToken: string): Promise<StravaActivity[]> {
  return apiFetch<StravaActivity[]>('/strava/activities', { token: authToken })
}

export async function disconnectStrava(authToken: string): Promise<void> {
  await apiFetch('/strava/disconnect', { token: authToken, method: 'DELETE' })
}
