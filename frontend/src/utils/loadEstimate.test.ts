import { describe, expect, it } from 'vitest'
import { DE } from '../i18n/de'
import type { FitUploadFileResult } from '../services/user'
import { loadEstimate } from './loadEstimate'

const imported = (overrides: Partial<FitUploadFileResult>): FitUploadFileResult => ({
  filename: 'ride.gpx',
  status: 'imported',
  message: 'Imported GPX activity',
  ...overrides,
})

describe('what an imported load was estimated from (ai-trainer-ops#311)', () => {
  it('names heart rate, a logged effort, and the duration', () => {
    expect(loadEstimate(imported({ loadSource: 'heart_rate' }))).toBe(
      'Load estimated from your heart rate: the file has no power.',
    )
    expect(loadEstimate(imported({ loadSource: 'rpe' }))).toBe('Load estimated from the effort you logged for that day.')
    expect(loadEstimate(imported({ loadSource: 'duration' }))).toBe(
      'Load estimated from the duration only: the file has neither power nor heart rate.',
    )
  })

  it('says why heart rate in the file still left the duration', () => {
    expect(loadEstimate(imported({ loadSource: 'duration', averageHeartRate: 140 }))).toBe(
      'Load estimated from the duration only. With your maximum heart rate in your profile, it would come from your heart rate.',
    )
  })

  it('says nothing about a measured load, a file not imported, or no source', () => {
    for (const loadSource of ['power', 'pace', 'provider', undefined, null]) {
      expect(loadEstimate(imported({ loadSource }))).toBeNull()
    }
    expect(loadEstimate(imported({ status: 'skipped', loadSource: 'duration' }))).toBeNull()
    expect(loadEstimate(imported({ status: 'failed', loadSource: 'heart_rate' }))).toBeNull()
  })

  it('has every sentence in German', () => {
    const sentences = [
      loadEstimate(imported({ loadSource: 'heart_rate' })),
      loadEstimate(imported({ loadSource: 'rpe' })),
      loadEstimate(imported({ loadSource: 'duration' })),
      loadEstimate(imported({ loadSource: 'duration', averageHeartRate: 140 })),
    ]
    for (const sentence of sentences) expect(DE[sentence!], sentence!).toBeDefined()
  })
})
