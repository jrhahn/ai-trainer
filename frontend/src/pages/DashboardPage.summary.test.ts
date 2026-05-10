import { describe, expect, it } from 'vitest'
import { splitTrainingSummary } from './DashboardPage'

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

  it('still parses plain text bullet summaries', () => {
    const parsed = splitTrainingSummary(
      'Intro sentence.\n- Plan Alignment: Keep tomorrow easy.\n- Next Action: Follow the plan.',
    )

    expect(parsed.intro).toBe('Intro sentence.')
    expect(parsed.bullets).toEqual([
      { text: 'Keep tomorrow easy.' },
      { label: 'Next Action', text: 'Follow the plan.' },
    ])
  })
})
