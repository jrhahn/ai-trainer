import type { StravaActivity } from '../store/useAppStore'
import { apiFetch } from './api'

interface StravaAuthResponse {
  authUrl: string
}

export async function getStravaAuthUrl(authToken: string): Promise<string> {
  const { authUrl } = await apiFetch<StravaAuthResponse>('/auth/strava', { token: authToken })
  return authUrl
}

export async function getStravaActivities(authToken: string): Promise<StravaActivity[]> {
  return apiFetch<StravaActivity[]>('/strava/activities', { token: authToken })
}

export async function getNewStravaActivities(
  authToken: string,
  afterId: number
): Promise<StravaActivity[]> {
  return apiFetch<StravaActivity[]>(`/strava/activities?after_id=${afterId}`, { token: authToken })
}

export async function disconnectStrava(authToken: string): Promise<void> {
  await apiFetch('/strava/disconnect', { token: authToken, method: 'DELETE' })
}
