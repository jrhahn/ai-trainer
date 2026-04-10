import type { StravaTokens, StravaActivity } from '../store/useAppStore'

/** Base URL of the Python OAuth backend (set via VITE_BACKEND_URL in .env). */
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL as string | undefined ?? 'http://localhost:8000').replace(/\/$/, '')

const STRAVA_API = 'https://www.strava.com'

/** URL that kicks off the Strava OAuth flow through the backend. */
export function getStravaAuthUrl(): string {
  return `${BACKEND_URL}/auth/strava`
}

export async function getStravaActivities(accessToken: string): Promise<StravaActivity[]> {
  const resp = await fetch(
    `${STRAVA_API}/api/v3/athlete/activities?per_page=10`,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  )
  if (!resp.ok) throw new Error('Failed to fetch Strava activities')
  return resp.json() as Promise<StravaActivity[]>
}

/** Refresh an expired access token via the backend (keeps client secret server-side). */
export async function refreshStravaToken(
  existingTokens: StravaTokens
): Promise<StravaTokens> {
  const resp = await fetch(`${BACKEND_URL}/auth/strava/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: existingTokens.refreshToken }),
  })
  if (!resp.ok) throw new Error('Failed to refresh Strava token')
  const data = await resp.json() as {
    access_token: string
    refresh_token: string
    expires_at: number
  }
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: data.expires_at,
    athleteId: existingTokens.athleteId,
    athleteName: existingTokens.athleteName,
  }
}
