import { describe, expect, it } from 'vitest'
import { GOAL_LABEL, initialForm, type PersistedProgress } from './form'
import type { UserProfile } from '../types/domain'

const none = { strava: null, intervals: null }
const profile = (fields: Partial<UserProfile>) => ({ trainingGoal: 'general_fitness', ...fields }) as UserProfile
const saved = (trainingGoal: unknown): PersistedProgress =>
  ({
    step: 2,
    trainingGoal,
    raceDate: '2027-05-01',
    raceDescription: 'Ötztaler',
    assessmentMethod: 'manual',
    fitnessLevel: null,
  }) as PersistedProgress

describe('the goal the onboarding form starts from (ai-trainer-ops#182)', () => {
  it('keeps each goal the profile holds, and a race date makes it a race', () => {
    expect(initialForm(profile({ trainingGoal: 'health' }), none, null).trainingGoal).toBe('health')
    expect(initialForm(profile({ trainingGoal: 'race' }), none, null).trainingGoal).toBe('race')
    expect(initialForm(profile({ raceDate: '2027-05-01' }), none, null).trainingGoal).toBe('race')
    expect(initialForm(null, none, null).trainingGoal).toBe('general_fitness')
  })

  it('keeps a saved goal it knows, and drops the race for any other goal', () => {
    const health = initialForm(null, none, saved('health'))
    expect(health.trainingGoal).toBe('health')
    expect(health.raceDate).toBe('')
    expect(initialForm(null, none, saved('race')).raceDate).toBe('2027-05-01')
    expect(initialForm(null, none, saved('ftp_improvement')).trainingGoal).toBe('general_fitness')
    expect(initialForm(null, none, saved(undefined)).trainingGoal).toBe('general_fitness')
  })

  it('names every goal', () => {
    expect(GOAL_LABEL).toEqual({ race: 'Race Prep', general_fitness: 'General Fitness', health: 'Stay Healthy' })
  })
})
