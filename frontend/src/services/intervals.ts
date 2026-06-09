import type { IntervalsConnection, StravaActivity } from '../store/useAppStore'
import { apiFetch } from './api'
import type { ImportProgress } from './strava'

interface IntervalsConnectionResponse {
  connected: boolean
  athleteId?: string | null
  athleteName?: string | null
}

function normalizeIntervalsConnection(response: IntervalsConnectionResponse): IntervalsConnection | null {
  if (!response.connected || !response.athleteId) return null
  return {
    athleteId: response.athleteId,
    athleteName: response.athleteName ?? null,
  }
}

export async function getIntervalsConnection(authToken: string): Promise<IntervalsConnection | null> {
  const response = await apiFetch<IntervalsConnectionResponse>('/intervals/connection', { token: authToken })
  return normalizeIntervalsConnection(response)
}

export async function saveIntervalsConnection(
  authToken: string,
  credentials: { apiKey: string; athleteId: string; athleteName?: string }
): Promise<IntervalsConnection> {
  const response = await apiFetch<IntervalsConnectionResponse>('/intervals/connection', {
    token: authToken,
    method: 'PUT',
    body: credentials,
  })
  const connection = normalizeIntervalsConnection(response)
  if (!connection) throw new Error('Intervals.icu connection was not saved')
  return connection
}

export async function disconnectIntervals(authToken: string): Promise<void> {
  await apiFetch('/intervals/connection', { token: authToken, method: 'DELETE' })
}

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
