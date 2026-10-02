import { describe, expect, it } from 'vitest'
import {
  PLAN_SPORT_CYCLING,
  PLAN_SPORT_RUNNING,
  PLAN_SPORT_STRENGTH,
  isCyclingSession,
  planSport,
  planSportLabel,
  planSportNoun,
} from './planSport'

describe('planSport', () => {
  it('reads an explicit sport', () => {
    expect(planSport({ sport: 'running' })).toBe(PLAN_SPORT_RUNNING)
  })

  it('defaults a day with no sport to cycling', () => {
    // Every plan written before #710 looks like this, and every cycling day
    // still does: the backend omits the default so storage stays byte-stable.
    expect(planSport({ workoutType: 'endurance' })).toBe(PLAN_SPORT_CYCLING)
    expect(planSport({})).toBe(PLAN_SPORT_CYCLING)
    expect(planSport(undefined)).toBe(PLAN_SPORT_CYCLING)
  })

  it('honours a legacy strength day, the one sport the old vocabulary could state', () => {
    expect(planSport({ workoutType: 'strength' })).toBe(PLAN_SPORT_STRENGTH)
  })

  it('does not read a purpose as a sport', () => {
    for (const workoutType of ['rest', 'intervals', 'tempo', 'race', 'recovery']) {
      expect(planSport({ workoutType })).toBe(PLAN_SPORT_CYCLING)
    }
  })

  it('an explicit sport wins over the legacy workout type', () => {
    expect(planSport({ sport: 'running', workoutType: 'strength' })).toBe(PLAN_SPORT_RUNNING)
  })
})

describe('isCyclingSession', () => {
  it('is what gates anything measured in watts', () => {
    expect(isCyclingSession({ workoutType: 'intervals' })).toBe(true)
    expect(isCyclingSession({ sport: 'cycling' })).toBe(true)
    expect(isCyclingSession({ sport: 'running' })).toBe(false)
    expect(isCyclingSession({ workoutType: 'strength' })).toBe(false)
  })
})

describe('naming', () => {
  it('uses the same nouns as a logged activity', () => {
    expect(planSportNoun({ sport: 'running' })).toBe('run')
    expect(planSportNoun({ workoutType: 'endurance' })).toBe('ride')
    expect(planSportNoun({ sport: 'strength' })).toBe('strength session')
  })

  it('labels every sport the plan editor offers', () => {
    expect(planSportLabel(PLAN_SPORT_CYCLING)).toBe('Cycling')
    expect(planSportLabel(PLAN_SPORT_RUNNING)).toBe('Running')
    expect(planSportLabel(PLAN_SPORT_STRENGTH)).toBe('Strength')
  })

  it('labels an unsupported sport rather than hiding it', () => {
    expect(planSportLabel('swim')).toBe('Swim')
  })

  it('says nothing when there is no sport to name', () => {
    // Stored plan data routinely has none, so this is a normal input.
    expect(planSportLabel('')).toBe('')
    expect(planSportLabel(null)).toBe('')
    expect(planSportLabel(undefined)).toBe('')
  })
})
