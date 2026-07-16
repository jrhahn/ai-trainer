import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'
import { describeEntry } from './planHistory'

// A non-conversation entry in the Coach Timeline (#418): a plan change the coach
// made, or a recommendation the learning pipeline surfaced. These are interleaved
// with the chat exchanges by time so the athlete reads what changed in context.
export type TimelineEventKind = 'plan-update' | 'open-question' | 'hypothesis' | 'experiment'

export interface TimelineEvent {
  id: string
  kind: TimelineEventKind
  timestamp: string
  title: string
  body: string
}

/** Applied plan-day changes become "Plan update" entries (blocked attempts are skipped). */
export function planUpdateEvents(entries: PlanDayHistoryEntry[]): TimelineEvent[] {
  return entries
    .filter((entry) => entry.applied)
    .map((entry) => ({
      id: `plan-${entry.id}`,
      kind: 'plan-update' as const,
      timestamp: entry.recordedAt,
      title: 'Plan update',
      body: describeEntry(entry),
    }))
}

/**
 * Open questions, proposed hypotheses and suggested experiments from the learning
 * pipeline become recommendation entries. Only still-actionable items are shown —
 * answered/refuted/dismissed/completed ones have already run their course.
 */
export function recommendationEvents(
  questions: AthleteOpenQuestion[],
  hypotheses: AthleteHypothesis[],
  experiments: AthleteExperiment[],
): TimelineEvent[] {
  const questionEvents: TimelineEvent[] = questions
    .filter((q) => q.status === 'open')
    .map((q) => ({
      id: `question-${q.id}`,
      kind: 'open-question',
      timestamp: q.firstAskedAt,
      title: 'Open question',
      body: q.question,
    }))
  const hypothesisEvents: TimelineEvent[] = hypotheses
    .filter((h) => h.status === 'proposed')
    .map((h) => ({
      id: `hypothesis-${h.id}`,
      kind: 'hypothesis',
      timestamp: h.firstProposedAt,
      title: 'Coach hypothesis',
      body: h.statement,
    }))
  const experimentEvents: TimelineEvent[] = experiments
    .filter((e) => e.status === 'suggested')
    .map((e) => ({
      id: `experiment-${e.id}`,
      kind: 'experiment',
      timestamp: e.createdAt,
      title: 'Suggested experiment',
      body: e.question,
    }))
  return [...questionEvents, ...hypothesisEvents, ...experimentEvents]
}
