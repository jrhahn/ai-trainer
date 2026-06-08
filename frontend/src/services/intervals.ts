import type { StravaActivity } from '../store/useAppStore'
import { apiFetch } from './api'
import type { ImportProgress } from './strava'

export async function getIntervalsActivities(authToken: string): Promise<StravaActivity[]> {
  return apiFetch<StravaActivity[]>('/intervals/activities', { token: authToken })
}

export async function getNewIntervalsActivities(
  authToken: string,
  afterId: number
): Promise<StravaActivity[]> {
  return apiFetch<StravaActivity[]>(`/intervals/activities?after_id=${afterId}`, { token: authToken })
}

export async function triggerIntervalsHistoryImport(
  authToken: string,
  months = 24
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/intervals/import-history?months=${months}`, {
    token: authToken,
    method: 'POST',
  })
}

export async function getIntervalsImportProgress(authToken: string): Promise<ImportProgress> {
  return apiFetch<ImportProgress>('/intervals/import-progress', { token: authToken })
}
