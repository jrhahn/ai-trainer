import type { StravaActivity } from '../types/domain'
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

export interface FailedActivity {
  activityId?: number
  activityName?: string
  activityDate?: string
  reason?: string
}

export interface ImportProgress {
  // 'interrupted': a restart of the app cut the import short (ai-trainer-ops#284)
  // 'analysing': the coach looks at the rides the connect imported (ai-trainer-ops#306)
  status: 'idle' | 'running' | 'analysing' | 'done' | 'error' | 'interrupted'
  total: number
  processed: number
  imported: number
  skipped: number
  failedActivities: FailedActivity[]
  error: string
  /** After an intervals.icu connect's import: whether the coach looked at the
   * rides, and how many days of the plan it changed (ai-trainer-ops#306). */
  analysed?: boolean | null
  planChanges?: number | null
}

export async function triggerStravaHistoryImport(
  authToken: string,
  months = 24,
  replaceExisting = false,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
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
