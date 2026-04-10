import type { StravaTokens, StravaActivity } from '../store/useAppStore'

const BASE = 'https://www.strava.com'

export function getStravaAuthUrl(clientId: string, redirectUri: string): string {
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    approval_prompt: 'force',
    scope: 'read,activity:read_all',
  })
  return `${BASE}/oauth/authorize?${params.toString()}`
}

export async function exchangeStravaToken(
  code: string,
  clientId: string,
  clientSecret: string
): Promise<StravaTokens> {
  const resp = await fetch(`${BASE}/oauth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      code,
      grant_type: 'authorization_code',
    }),
  })
  if (!resp.ok) throw new Error('Failed to exchange Strava token')
  const data = await resp.json() as {
    access_token: string
    refresh_token: string
    expires_at: number
    athlete: { id: number; firstname: string; lastname: string }
  }
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: data.expires_at,
    athleteId: data.athlete.id,
    athleteName: `${data.athlete.firstname} ${data.athlete.lastname}`,
  }
}

export async function getStravaActivities(accessToken: string): Promise<StravaActivity[]> {
  const resp = await fetch(
    `${BASE}/api/v3/athlete/activities?per_page=10`,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  )
  if (!resp.ok) throw new Error('Failed to fetch Strava activities')
  return resp.json() as Promise<StravaActivity[]>
}

export async function refreshStravaToken(
  refreshToken: string,
  clientId: string,
  clientSecret: string
): Promise<StravaTokens> {
  const resp = await fetch(`${BASE}/oauth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: refreshToken,
      grant_type: 'refresh_token',
    }),
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
    athleteId: 0,
    athleteName: '',
  }
}
