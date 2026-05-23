import { describe, expect, it } from 'vitest'
import { splitTrainingSummary, formatDuration, computeMatchScore, buildMatchCoachPrompt } from './DashboardPage'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'

describe('formatDuration', () => {
  it('returns empty string for undefined', () => {
    expect(formatDuration(undefined)).toBe('')
  })

  it('returns empty string for zero seconds', () => {
    expect(formatDuration(0)).toBe('')
  })

  it('formats a minutes-only duration', () => {
    expect(formatDuration(45 * 60)).toBe('45 min')
  })

  it('formats hours and minutes', () => {
    expect(formatDuration(90 * 60)).toBe('1h 30m')
  })

  it('formats exact hours with zero minutes', () => {
    expect(formatDuration(2 * 3600)).toBe('2h 0m')
  })
})

describe('splitTrainingSummary', () => {
  it('parses JSON summary payloads with intro and bulletPoints', () => {
    const summary = JSON.stringify({
      intro: "Here's a quick look at your recent training, Jürgen.",
      bulletPoints: [
        '- Recent Ride: You completed a very demanding mountain bike ride, nearly 4.5 hours with close to 1900m of climbing.\n\n    This shows your excellent capacity for long, hard efforts in challenging terrain.',
        '- Effort &amp; Recovery: The ride was far more intense than your planned recovery session.\n    While you put out impressive power, the consistent heart rate drift suggests your body was working very hard.\n    Immediate and diligent recovery is paramount.',
        '- Plan Alignment: This demanding ride significantly overshot your planned recovery day.\n    We need to adjust the immediate training to prevent over-fatigue before your target race.',
        '- Next Action: Strictly adhere to the modified plan for tomorrow to ensure you are fully recovered and primed for your upcoming race.',
      ],
    })

    const parsed = splitTrainingSummary(summary)

    expect(parsed.intro).toBe("Here's a quick look at your recent training, Jürgen.")
    expect(parsed.bullets).toHaveLength(4)
    expect(parsed.bullets[0]).toEqual({
      label: 'Recent Ride',
      text: 'You completed a very demanding mountain bike ride, nearly 4.5 hours with close to 1900m of climbing. This shows your excellent capacity for long, hard efforts in challenging terrain.',
    })
    expect(parsed.bullets[1]).toEqual({
      label: 'Effort & Recovery',
      text: 'The ride was far more intense than your planned recovery session. While you put out impressive power, the consistent heart rate drift suggests your body was working very hard. Immediate and diligent recovery is paramount.',
    })
  })

  it('parses JSON summary payloads with intro and bullets', () => {
    const summary = JSON.stringify({
      intro: 'Great to see your recent effort. Here’s a quick summary of your training:',
      bullets: [
        '- Ride Category: Your latest mountain bike ride was a highly demanding mixed-interval session.',
        '- Next Steps: Prioritizing rest and recovery is essential.',
      ],
    })

    const parsed = splitTrainingSummary(summary)

    expect(parsed.intro).toBe('Great to see your recent effort. Here’s a quick summary of your training:')
    expect(parsed.bullets).toEqual([
      {
        label: 'Ride Category',
        text: 'Your latest mountain bike ride was a highly demanding mixed-interval session.',
      },
      { label: 'Next Steps', text: 'Prioritizing rest and recovery is essential.' },
    ])
  })

  it('parses legacy JSON object summaries as bullet values', () => {
    const summary = JSON.stringify({
      '1_WHAT_YOU_DID': 'Over the past week you completed two rides totalling 3h.',
      '2_PLAN_ALIGNMENT': 'You hit your endurance session well.',
    })

    const parsed = splitTrainingSummary(summary)

    expect(parsed.intro).toBe('')
    expect(parsed.bullets).toEqual([
      { text: 'Over the past week you completed two rides totalling 3h.' },
      { text: 'You hit your endurance session well.' },
    ])
  })

  it('still parses plain text bullet summaries', () => {
    const parsed = splitTrainingSummary(
      'Intro sentence.\n- Plan Alignment: Keep tomorrow easy.\n- Next Action: Follow the plan.',
    )

    expect(parsed.intro).toBe('Intro sentence.')
    expect(parsed.bullets).toEqual([
      { label: 'Plan Alignment', text: 'Keep tomorrow easy.' },
      { label: 'Next Action', text: 'Follow the plan.' },
    ])
  })
})

// ---------------------------------------------------------------------------
// computeMatchScore
// ---------------------------------------------------------------------------

const basePlan: Partial<TrainingDay> = {
  workoutType: 'intervals',
  title: 'VO2max',
  durationMinutes: 60,
  targetPower: { low: 280, high: 320 },
}

function makeRideForScore(overrides: Partial<RideMetricPoint> = {}): RideMetricPoint {
  return {
    stravaActivityId: 1,
    activityDate: '2026-05-20',
    sportType: 'Ride',
    ...overrides,
  }
}

describe('computeMatchScore', () => {
  it('returns null when ride has no duration and plan has no power target', () => {
    expect(computeMatchScore(makeRideForScore(), {})).toBeNull()
  })

  it('returns 100 when duration matches exactly and power is in zone', () => {
    const ride = makeRideForScore({ durationSeconds: 60 * 60, normalizedPowerW: 300 })
    expect(computeMatchScore(ride, basePlan)).toBe(100)
  })

  it('scores duration-only when no power target exists', () => {
    const ride = makeRideForScore({ durationSeconds: 60 * 60 })
    const score = computeMatchScore(ride, { durationMinutes: 60 })
    expect(score).toBe(100)
  })

  it('penalises duration deviation', () => {
    // Actual 48 min vs planned 60 min (−20 %) → ratio = 0.8 → score = max(0, 100 − 40) = 60
    const ride = makeRideForScore({ durationSeconds: 48 * 60 })
    const score = computeMatchScore(ride, { durationMinutes: 60 })
    expect(score).toBe(60)
  })

  it('returns 0 when actual duration is half the planned', () => {
    const ride = makeRideForScore({ durationSeconds: 30 * 60 })
    const score = computeMatchScore(ride, { durationMinutes: 60 })
    expect(score).toBe(0)
  })

  it('penalises power outside the target zone', () => {
    // NP = 350 W, zone high = 320 W → deviation = 30/320 ≈ 9.4 % → score = max(0, 100 − 28) = 72
    const ride = makeRideForScore({ normalizedPowerW: 350, durationSeconds: 60 * 60 })
    const score = computeMatchScore(ride, basePlan)
    expect(score).toBeGreaterThan(0)
    expect(score).toBeLessThan(90)
  })

  it('averages duration and power components', () => {
    // Perfect duration (100), power out of zone → average < 100
    const ride = makeRideForScore({ durationSeconds: 60 * 60, normalizedPowerW: 400 })
    const score = computeMatchScore(ride, basePlan)
    expect(score).not.toBeNull()
    expect(score!).toBeLessThan(100)
  })

  it('falls back to avgPowerW when normalizedPowerW is absent', () => {
    const ride = makeRideForScore({ avgPowerW: 300, durationSeconds: 60 * 60 })
    expect(computeMatchScore(ride, basePlan)).toBe(100)
  })
})

// ---------------------------------------------------------------------------
// buildMatchCoachPrompt
// ---------------------------------------------------------------------------

describe('buildMatchCoachPrompt', () => {
  it('includes the ride name in the prompt', () => {
    const ride = makeRideForScore({ activityName: 'Morning Blast', durationSeconds: 60 * 60 })
    const prompt = buildMatchCoachPrompt(ride, basePlan, 85)
    expect(prompt).toContain('Morning Blast')
  })

  it('includes the plan title in the prompt', () => {
    const ride = makeRideForScore({ durationSeconds: 60 * 60 })
    const prompt = buildMatchCoachPrompt(ride, basePlan, 85)
    expect(prompt).toContain('VO2max')
  })

  it('includes the score when provided', () => {
    const ride = makeRideForScore({ durationSeconds: 60 * 60 })
    const prompt = buildMatchCoachPrompt(ride, basePlan, 72)
    expect(prompt).toContain('72%')
  })

  it('omits score wording when score is null', () => {
    const ride = makeRideForScore({ durationSeconds: 60 * 60 })
    const prompt = buildMatchCoachPrompt(ride, { title: 'Easy Spin' }, null)
    expect(prompt).not.toContain('%')
  })

  it('handles a missing ride name gracefully', () => {
    const ride = makeRideForScore({ activityName: undefined })
    const prompt = buildMatchCoachPrompt(ride, basePlan, 50)
    expect(prompt).toContain('my ride')
  })
})
