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

export interface ImportProgress {
  jobId?: string | null
  status: 'idle' | 'running' | 'done' | 'error'
  total: number
  processed: number
  imported: number
  skipped: number
  failedActivities: Array<{
    activityId?: number | null
    activityName?: string | null
    activityDate?: string | null
    reason: string
  }>
  error: string
}

export async function triggerStravaHistoryImport(
  authToken: string,
  months = 24,
  replaceExisting = false,
): Promise<{ status: string; jobId?: string }> {
  return apiFetch<{ status: string; jobId?: string }>(
    `/strava/import-history?months=${months}&replace_existing=${replaceExisting ? 'true' : 'false'}`,
    {
      token: authToken,
      method: 'POST',
    }
  )
}

export async function getStravaImportProgress(authToken: string): Promise<ImportProgress> {
  return apiFetch<ImportProgress>('/strava/import-progress', { token: authToken })
}
