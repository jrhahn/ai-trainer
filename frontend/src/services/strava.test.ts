import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import {
  getStravaAuthUrl,
  getStravaActivities,
  getNewStravaActivities,
  disconnectStrava,
  triggerStravaHistoryImport,
} from './strava'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('getStravaAuthUrl', () => {
  it('returns the authorization URL from the backend', async () => {
    mockApiFetch.mockResolvedValue({ authUrl: 'https://www.strava.com/oauth/authorize?client_id=123' })

    const url = await getStravaAuthUrl('tok-123')

    expect(url).toBe('https://www.strava.com/oauth/authorize?client_id=123')
    expect(mockApiFetch).toHaveBeenCalledWith('/auth/strava', { token: 'tok-123' })
  })
})

describe('getStravaActivities', () => {
  it('returns the list of Strava activities', async () => {
    const activities = [
      {
        id: 1,
        name: 'Morning Ride',
        type: 'Ride',
        distance: 40000,
        moving_time: 3600,
        elapsed_time: 3700,
        total_elevation_gain: 400,
        start_date: '2024-05-01T08:00:00Z',
      },
    ]
    mockApiFetch.mockResolvedValue(activities)

    const result = await getStravaActivities('tok-123')

    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('Morning Ride')
    expect(mockApiFetch).toHaveBeenCalledWith('/strava/activities', { token: 'tok-123' })
  })
})

describe('getNewStravaActivities', () => {
  it('fetches activities after the given ID', async () => {
    mockApiFetch.mockResolvedValue([])

    await getNewStravaActivities('tok-123', 42)

    expect(mockApiFetch).toHaveBeenCalledWith('/strava/activities?after_id=42', { token: 'tok-123' })
  })
})

describe('disconnectStrava', () => {
  it('calls DELETE on the disconnect endpoint', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await disconnectStrava('tok-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/strava/disconnect', {
      token: 'tok-123',
      method: 'DELETE',
    })
  })
})

describe('triggerStravaHistoryImport', () => {
  it('calls import-history with overwrite flag when requested', async () => {
    mockApiFetch.mockResolvedValue({ status: 'started' })

    await triggerStravaHistoryImport('tok-123', 24, true)

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/strava/import-history?months=24&replace_existing=true',
      {
        token: 'tok-123',
        method: 'POST',
      }
    )
  })
})
