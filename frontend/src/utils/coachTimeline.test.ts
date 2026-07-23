import { describe, expect, it } from 'vitest'
import { planUpdateEvents, recommendationEvents } from './coachTimeline'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'

function entry(overrides: Partial<PlanDayHistoryEntry> = {}): PlanDayHistoryEntry {
  return {
    id: 'p1',
    date: '2026-06-15',
    source: 'coach_chat',
    applied: true,
    recordedAt: '2026-06-15T10:00:00.000Z',
    batchId: 'b1',
    oldDay: null,
    newDay: { workoutType: 'recovery', title: 'Recovery Ride' },
    ...overrides,
  }
}

describe('planUpdateEvents', () => {
  it('maps a single-day run to one detailed timeline event', () => {
    const events = planUpdateEvents([entry()])
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({
      id: 'plan-b1',
      kind: 'plan-update',
      timestamp: '2026-06-15T10:00:00.000Z',
      title: 'Coach chat',
    })
    expect(events[0].body).toContain('Recovery Ride')
  })

  it('collapses a multi-day run into one card with a breakdown (#435)', () => {
    const events = planUpdateEvents([
      entry({ id: 'a', source: 'generate', batchId: 'gen1' }),
      entry({
        id: 'b',
        source: 'generate',
        batchId: 'gen1',
        recordedAt: '2026-06-15T10:00:01.000Z',
        oldDay: { workoutType: 'endurance', title: 'Base' },
        newDay: { workoutType: 'recovery', title: 'Spin' },
      }),
      entry({
        id: 'c',
        source: 'generate',
        batchId: 'gen1',
        oldDay: { workoutType: 'rest', title: 'Rest' },
        newDay: null,
      }),
    ])
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({
      id: 'plan-gen1',
      title: 'Plan generation',
      // Placed by the most recent row in the run.
      timestamp: '2026-06-15T10:00:01.000Z',
    })
    expect(events[0].body).toBe('3 days updated (1 changed, 1 added, 1 removed)')
  })

  it('omits empty buckets from a changed-only run breakdown', () => {
    const modified = (id: string): PlanDayHistoryEntry =>
      entry({
        id,
        source: 'generate',
        batchId: 'gen2',
        oldDay: { workoutType: 'endurance', title: 'Base' },
        newDay: { workoutType: 'tempo', title: 'Tempo' },
      })
    const events = planUpdateEvents([modified('a'), modified('b')])
    expect(events).toHaveLength(1)
    expect(events[0].body).toBe('2 days updated (2 changed)')
  })

  it('groups legacy rows without a batchId by source + timestamp', () => {
    const events = planUpdateEvents([
      entry({ id: 'a', batchId: null }),
      entry({ id: 'b', batchId: null }),
    ])
    expect(events).toHaveLength(1)
    expect(events[0].id).toBe('plan-a')
    expect(events[0].body).toBe('2 days updated (2 added)')
  })

  it('skips blocked (unapplied) changes — they kept the athlete version', () => {
    expect(planUpdateEvents([entry({ id: 'p2', applied: false })])).toEqual([])
  })

  it('drops narrated runs — their what/why is told in the chat message (#439)', () => {
    // A narrated nightly run: two applied rows, both flagged narrated.
    const narrated = planUpdateEvents([
      entry({ id: 'a', source: 'nightly_maintenance', batchId: 'n1', narrated: true }),
      entry({ id: 'b', source: 'nightly_maintenance', batchId: 'n1', narrated: true }),
    ])
    expect(narrated).toEqual([])
    // A non-narrated generation still collapses into a card.
    const events = planUpdateEvents([
      entry({ id: 'c', source: 'generate', batchId: 'gen3', narrated: false }),
    ])
    expect(events).toHaveLength(1)
    expect(events[0].id).toBe('plan-gen3')
  })
})

function question(overrides: Partial<AthleteOpenQuestion> = {}): AthleteOpenQuestion {
  return {
    id: 'q1',
    question: 'Recover faster with a rest day?',
    category: 'recovery',
    evidence: '',
    needs: '',
    evidenceCount: 1,
    status: 'open',
    resolution: null,
    firstAskedAt: '2026-06-15T09:00:00.000Z',
    updatedAt: '2026-06-15T09:00:00.000Z',
    ...overrides,
  }
}

function hypothesis(overrides: Partial<AthleteHypothesis> = {}): AthleteHypothesis {
  return {
    id: 'h1',
    statement: 'Threshold holds in the heat.',
    category: 'physiology',
    rationale: 'Hot-ride power is steady.',
    confidence: 0.6,
    evidenceCount: 3,
    status: 'proposed',
    firstProposedAt: '2026-06-15T08:00:00.000Z',
    updatedAt: '2026-06-15T08:00:00.000Z',
    ...overrides,
  }
}

function experiment(overrides: Partial<AthleteExperiment> = {}): AthleteExperiment {
  return {
    id: 'e1',
    hypothesisId: null,
    question: 'Does a long warmup help VO2 efforts?',
    protocol: 'Add a long warmup for two weeks.',
    rationale: 'Testing readiness.',
    category: 'training',
    status: 'suggested',
    createdAt: '2026-06-15T07:00:00.000Z',
    updatedAt: '2026-06-15T07:00:00.000Z',
    ...overrides,
  }
}

describe('recommendationEvents', () => {
  it('surfaces only still-actionable recommendations, keyed by kind', () => {
    const events = recommendationEvents([question()], [hypothesis()], [experiment()])
    expect(events.map((e) => e.kind)).toEqual(['open-question', 'hypothesis', 'experiment'])
    expect(events.map((e) => e.title)).toEqual([
      'Open question',
      'Coach hypothesis',
      'Suggested experiment',
    ])
    expect(events.map((e) => e.body)).toEqual([
      'Recover faster with a rest day?',
      'Threshold holds in the heat.',
      'Does a long warmup help VO2 efforts?',
    ])
  })

  it('drops answered / refuted / completed items that have run their course', () => {
    const events = recommendationEvents(
      [question({ status: 'answered' }), question({ id: 'q2', status: 'dismissed' })],
      [hypothesis({ status: 'refuted' }), hypothesis({ id: 'h2', status: 'confirmed' })],
      [experiment({ status: 'completed' }), experiment({ id: 'e2', status: 'dismissed' })],
    )
    expect(events).toEqual([])
  })
})
