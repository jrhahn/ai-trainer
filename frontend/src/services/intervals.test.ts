import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiFetch = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ apiFetch: mockApiFetch }))

import {
  getIntervalsConnection,
  saveIntervalsConnection,
  disconnectIntervals,
  getIntervalsActivities,
  getNewIntervalsActivities,
  triggerIntervalsHistoryImport,
  getIntervalsImportProgress,
} from './intervals'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('getIntervalsConnection', () => {
  it('returns the normalized connection when connected', async () => {
    mockApiFetch.mockResolvedValue({
      connected: true,
      athleteId: 'i1234',
      athleteName: 'Jane',
    })

    const result = await getIntervalsConnection('tok-123')

    expect(result).toEqual({ athleteId: 'i1234', athleteName: 'Jane' })
    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/connection', { token: 'tok-123' })
  })

  it('defaults athleteName to null when absent', async () => {
    mockApiFetch.mockResolvedValue({ connected: true, athleteId: 'i1234' })

    const result = await getIntervalsConnection('tok-123')

    expect(result).toEqual({ athleteId: 'i1234', athleteName: null })
  })

  it('returns null when not connected', async () => {
    mockApiFetch.mockResolvedValue({ connected: false })

    expect(await getIntervalsConnection('tok-123')).toBeNull()
  })

  it('returns null when connected but athleteId is missing', async () => {
    mockApiFetch.mockResolvedValue({ connected: true, athleteId: null })

    expect(await getIntervalsConnection('tok-123')).toBeNull()
  })
})

describe('saveIntervalsConnection', () => {
  it('PUTs the credentials and returns the saved connection', async () => {
    mockApiFetch.mockResolvedValue({
      connected: true,
      athleteId: 'i1234',
      athleteName: 'Jane',
    })

    const result = await saveIntervalsConnection('tok-123', {
      apiKey: 'key',
      athleteId: 'i1234',
      athleteName: 'Jane',
    })

    expect(result).toEqual({ athleteId: 'i1234', athleteName: 'Jane' })
    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/connection', {
      token: 'tok-123',
      method: 'PUT',
      body: { apiKey: 'key', athleteId: 'i1234', athleteName: 'Jane' },
    })
  })

  it('throws when the backend response is not a valid connection', async () => {
    mockApiFetch.mockResolvedValue({ connected: false })

    await expect(
      saveIntervalsConnection('tok-123', { apiKey: 'key', athleteId: 'i1234' })
    ).rejects.toThrow('Intervals.icu connection was not saved')
  })
})

describe('disconnectIntervals', () => {
  it('calls DELETE on the connection endpoint', async () => {
    mockApiFetch.mockResolvedValue(undefined)

    await disconnectIntervals('tok-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/connection', {
      token: 'tok-123',
      method: 'DELETE',
    })
  })
})

describe('getIntervalsActivities', () => {
  it('returns the list of activities', async () => {
    mockApiFetch.mockResolvedValue([{ id: 1, name: 'Ride' }])

    const result = await getIntervalsActivities('tok-123')

    expect(result).toHaveLength(1)
    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/activities', { token: 'tok-123' })
  })
})

describe('getNewIntervalsActivities', () => {
  it('fetches activities after the given ID', async () => {
    mockApiFetch.mockResolvedValue([])

    await getNewIntervalsActivities('tok-123', 42)

    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/activities?after_id=42', {
      token: 'tok-123',
    })
  })
})

describe('triggerIntervalsHistoryImport', () => {
  it('defaults to 24 months', async () => {
    mockApiFetch.mockResolvedValue({ status: 'started' })

    await triggerIntervalsHistoryImport('tok-123')

    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/import-history?months=24', {
      token: 'tok-123',
      method: 'POST',
    })
  })

  it('passes a custom month count', async () => {
    mockApiFetch.mockResolvedValue({ status: 'started' })

    await triggerIntervalsHistoryImport('tok-123', 6)

    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/import-history?months=6', {
      token: 'tok-123',
      method: 'POST',
    })
  })
})

describe('getIntervalsImportProgress', () => {
  it('returns the import progress', async () => {
    mockApiFetch.mockResolvedValue({ status: 'running', processed: 10, total: 20 })

    const result = await getIntervalsImportProgress('tok-123')

    expect(result).toEqual({ status: 'running', processed: 10, total: 20 })
    expect(mockApiFetch).toHaveBeenCalledWith('/intervals/import-progress', { token: 'tok-123' })
  })
})
