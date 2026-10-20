import { describe, expect, it } from 'vitest'
import { alreadyRecorded, parseReportedSession } from './reportedSession'

const today = new Date(2026, 9, 20)

describe('parseReportedSession (ai-trainer-ops#166)', () => {
  it('reads the ride from the walkthrough', () => {
    expect(
      parseReportedSession('so today starting with a base spin? i did that. 30km on a road bike, 1h 200w np', today)
    ).toEqual({
      date: '2026-10-20',
      sport: 'cycling',
      durationMinutes: 60,
      distanceKm: 30,
      normalizedPower: 200,
    })
  })

  it('reads German, yesterday, minutes and the heart rate', () => {
    expect(parseReportedSession('Bin gestern 45 min gelaufen, 8,5 km, Puls 152', today)).toEqual({
      date: '2026-10-19',
      sport: 'running',
      durationMinutes: 45,
      distanceKm: 8.5,
      averageHeartRate: 152,
    })
  })

  it('reads hours with minutes, decimal hours and bpm', () => {
    expect(parseReportedSession('rode 1h30, 140 bpm', today)).toMatchObject({ durationMinutes: 90, averageHeartRate: 140 })
    expect(parseReportedSession('did 1:15 h on the trainer', today)).toMatchObject({ durationMinutes: 75 })
    expect(parseReportedSession('Habe 1,5 Stunden Rad gemacht', today)).toMatchObject({ durationMinutes: 90 })
    expect(parseReportedSession('done: 2 hours, NP of 210', today)).toMatchObject({ durationMinutes: 120, normalizedPower: 210 })
  })

  it('takes a gym session without distance, power or heart rate', () => {
    expect(parseReportedSession('did 50 min in the gym, 30 km away, 200w np, hr 120', today)).toEqual({
      date: '2026-10-20',
      sport: 'strength',
      durationMinutes: 50,
    })
  })

  it('keeps a run\'s watts out', () => {
    expect(parseReportedSession('ran 40 min, 250w np', today)).not.toHaveProperty('normalizedPower')
  })

  it('stays quiet on plans, questions and talk without a number', () => {
    for (const text of [
      'I will ride 2h tomorrow',
      'should I do 90 min today?',
      'Morgen fahre ich 60 km',
      'I did the intervals, they felt hard',
      'what is a good NP for 1h?',
    ]) {
      expect(parseReportedSession(text, today), text).toBeNull()
    }
  })

  it('drops numbers outside what a session can be', () => {
    expect(parseReportedSession('did 1h, 5000w np, hr 400', today)).toEqual({
      date: '2026-10-20',
      sport: 'cycling',
      durationMinutes: 60,
    })
  })
})

describe('alreadyRecorded', () => {
  const session = { date: '2026-10-20', sport: 'cycling' as const }

  it('is true for a synced ride that day, not for an entered one or another sport', () => {
    expect(alreadyRecorded(session, [{ stravaActivityId: 1, activityDate: '2026-10-20', sportType: 'Ride' }])).toBe(true)
    expect(alreadyRecorded(session, [{ stravaActivityId: 1, activityDate: '2026-10-20', sportType: 'Run' }])).toBe(false)
    expect(alreadyRecorded(session, [{ stravaActivityId: 1, activityDate: '2026-10-19', sportType: 'Ride' }])).toBe(false)
    expect(
      alreadyRecorded(session, [
        {
          stravaActivityId: 1,
          activityDate: '2026-10-20',
          sportType: 'cycling',
          activitySource: 'logged',
          externalActivityId: '2026-10-20#100',
        },
      ])
    ).toBe(false)
  })
})
