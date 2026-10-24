import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DE } from './de'
import { syncErrorMessage } from '../hooks/useStravaSync'
import { matchScoreLabel } from '../pages/DashboardPage'
import { REASONING_SOURCE_META } from '../utils/reasoningSource'
import type { TrainingDay } from '../store/useAppStore'

// Strings that reach t() as values rather than literals translate only when
// de.ts has their exact key, and a missing one falls back to English without a
// word (review of #950, ai-trainer-ops#121). Each producer is walked here.
describe('every dynamically translated string has a German key', () => {
  it('the four sync error sentences', () => {
    const refused = Object.assign(new Error('x'), { status: 403 })
    const other = new Error('x')
    for (const source of ['strava', 'intervals'] as const) {
      for (const error of [refused, other]) {
        const sentence = syncErrorMessage(source, error)
        expect(DE[sentence], sentence).toBeDefined()
      }
    }
  })

  it('every compliance badge label', () => {
    const plans: Partial<TrainingDay>[] = [
      { workoutType: 'endurance', durationMinutes: 60 },
      { workoutType: 'rest' },
      { workoutType: 'strength', durationMinutes: 45 },
    ]
    const labels = new Set<string>()
    for (const plan of plans) {
      for (let score = 0; score <= 100; score += 1) labels.add(matchScoreLabel(score, plan))
    }
    labels.delete('?')
    expect(labels.size).toBeGreaterThanOrEqual(12)
    for (const label of labels) expect(DE[label], label).toBeDefined()
  })

  it('every reasoning-source label', () => {
    for (const { label } of Object.values(REASONING_SOURCE_META)) expect(DE[label], label).toBeDefined()
  })

  it('every week focus the season plan sends', () => {
    // Fixed English phrases from the backend, shown as "This week: Base ·
    // aerobic endurance". They stayed English in German until 2026-10-24.
    const backend = (file: string) => readFileSync(resolve(__dirname, '../../../backend/services', file), 'utf8')
    const season = backend('season_plan.py')
    const phase = backend('training_phase.py')
    const block = (source: string, name: string) => source.slice(source.indexOf(`${name} = `)).split(/\n[)}]/)[0]
    const quoted = (text: string) => [...text.matchAll(/"([^"]+)"/g)].map((m) => m[1])
    const foci = [
      ...quoted(block(season, 'PHASE_FOCUS')),
      ...quoted(season.match(/^EARLY_BUILD_FOCUS = .*$/m)![0]),
      ...quoted(season.match(/^LATE_BUILD_FOCUS = .*$/m)![0]),
      ...quoted(block(phase, 'MAINTENANCE_FOCI')),
    ]
    expect(foci.length).toBeGreaterThanOrEqual(9)
    for (const focus of foci) expect(DE[focus], focus).toBeDefined()
  })

  it('every name the store gives a failed load', () => {
    const store = readFileSync(resolve(__dirname, '../store/useAppStore.ts'), 'utf8')
    const names = [...store.matchAll(/failed\.push\('([^']+)'\)/g)].map((m) => m[1])
    expect(names.length).toBeGreaterThanOrEqual(5)
    for (const name of names) expect(DE[name], name).toBeDefined()
  })
})
