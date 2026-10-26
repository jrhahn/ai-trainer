import type { IntervalsConnection, StravaActivity } from '../types/domain'
import { apiFetch } from './api'
import type { ImportProgress } from './strava'

interface IntervalsConnectionResponse {
  connected: boolean
  athleteId?: string | null
  athleteName?: string | null
  keyHint?: string | null
}

function normalizeIntervalsConnection(response: IntervalsConnectionResponse): IntervalsConnection | null {
  if (!response.connected || !response.athleteId) return null
  return {
    athleteId: response.athleteId,
    athleteName: response.athleteName ?? null,
    keyHint: response.keyHint ?? null,
  }
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

/** What the last month at intervals.icu holds for the coach (owner, 2026-10-21). */
export interface IntervalsCheck {
  /** Rides the app can read: a device synced directly, or an upload. */
  usable: number
  /** Rides intervals.icu got from Strava and may not pass on. */
  fromStrava: number
}

export async function checkIntervals(authToken: string): Promise<IntervalsCheck> {
  return apiFetch<IntervalsCheck>('/intervals/check', { token: authToken })
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

/** The days a connect imports: the four weeks an athlete thinks of, and the 42
 * days the fitness value is built from (ai-trainer-ops#306). */
export const CONNECT_IMPORT_DAYS = 42

/** The import a connect starts: the last six weeks, then the coach's one look
 * at them, which adapts the plan and says so in the chat (ai-trainer-ops#306). */
export async function startIntervalsConnectImport(authToken: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/intervals/import-history?days=${CONNECT_IMPORT_DAYS}&analyse=true`, {
    token: authToken,
    method: 'POST',
  })
}

export async function getIntervalsImportProgress(authToken: string): Promise<ImportProgress> {
  return apiFetch<ImportProgress>('/intervals/import-progress', { token: authToken })
}
