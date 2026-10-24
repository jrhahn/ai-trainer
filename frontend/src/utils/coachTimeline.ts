import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'
import { sourceLabel, summarizeDayChange } from './planHistory'
import { english, type Translate } from '../i18n'
import { parseLocalDate } from './workout'

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
  /** One line per day of a multi-day run, shown on request (ai-trainer-ops#91). */
  details?: string[]
}

/**
 * One concise sentence for a whole coach run. A single-day run keeps its detailed
 * field diff ("type … → …, duration … → … min"); a multi-day run collapses to a
 * count with a short breakdown so a 21-day plan generation is one line, not 21
 * (#435). The full per-day detail stays in the plan-history DB for debugging.
 */
function summarizeBatch(entries: PlanDayHistoryEntry[], t: Translate): string {
  if (entries.length === 1) {
    return summarizeDayChange(entries[0].oldDay, entries[0].newDay, t)
  }
  let added = 0
  let removed = 0
  let changed = 0
  for (const e of entries) {
    if (!e.oldDay) added += 1
    else if (!e.newDay) removed += 1
    else changed += 1
  }
  const parts: string[] = []
  if (changed) parts.push(t('{n} changed', { n: changed }))
  if (added) parts.push(t('{n} added', { n: added }))
  if (removed) parts.push(t('{n} removed', { n: removed }))
  // Every entry falls into exactly one bucket, so with ≥2 entries `parts` is
  // always non-empty — no empty-breakdown branch to guard.
  return t('{count} days updated ({parts})', { count: entries.length, parts: parts.join(', ') })
}

/**
 * Applied plan-day changes become "plan update" entries (blocked attempts are
 * skipped). Rows are grouped by their coach run (`batchId`) so one plan
 * generation / nightly tune-up is a single card instead of one per changed day.
 * Rows without a `batchId` (written before that column existed) fall back to
 * grouping by source + timestamp so legacy history still collapses sensibly.
 *
 * Runs that were narrated as a coach chat message (`narrated`, #439) are dropped
 * here: their "what and why" is already told in the athlete's chat feed, so a
 * timeline card would be a duplicate entry.
 */
export function planUpdateEvents(
  entries: PlanDayHistoryEntry[],
  t: Translate = english,
  locale?: string,
): TimelineEvent[] {
  const applied = entries.filter((entry) => entry.applied && !entry.narrated)
  const groups = new Map<string, PlanDayHistoryEntry[]>()
  for (const entry of applied) {
    const key = entry.batchId ?? `${entry.source}|${entry.recordedAt}`
    const group = groups.get(key)
    if (group) {
      group.push(entry)
    } else {
      groups.set(key, [entry])
    }
  }
  return [...groups.values()].map((group) => ({
    id: `plan-${group[0].batchId ?? group[0].id}`,
    kind: 'plan-update' as const,
    // A run is placed by its most recent row so it sorts correctly in the feed.
    timestamp: group.reduce(
      (latest, e) => (e.recordedAt > latest ? e.recordedAt : latest),
      group[0].recordedAt,
    ),
    title: sourceLabel(group[0].source, t),
    body: summarizeBatch(group, t),
    details:
      group.length > 1
        ? [...group]
            .sort((a, b) => a.date.localeCompare(b.date))
            .map((e) => `${dayLabel(e.date, locale)}: ${summarizeDayChange(e.oldDay, e.newDay, t)}`)
        : undefined,
  }))
}

function dayLabel(date: string, locale?: string): string {
  return parseLocalDate(date).toLocaleDateString(locale, { weekday: 'short', day: 'numeric', month: 'short' })
}

/**
 * Open questions, proposed hypotheses and suggested experiments from the learning
 * pipeline become recommendation entries. Only still-actionable items are shown —
 * answered/refuted/dismissed/completed ones have already run their course.
 * `shown` gives a text in the interface language: they are stored in English
 * (ai-trainer-ops#215).
 */
export function recommendationEvents(
  questions: AthleteOpenQuestion[],
  hypotheses: AthleteHypothesis[],
  experiments: AthleteExperiment[],
  t: Translate = english,
  shown: (text: string) => string = (text) => text,
): TimelineEvent[] {
  const questionEvents: TimelineEvent[] = questions
    .filter((q) => q.status === 'open')
    .map((q) => ({
      id: `question-${q.id}`,
      kind: 'open-question',
      timestamp: q.firstAskedAt,
      title: t('Open question'),
      body: shown(q.question),
    }))
  const hypothesisEvents: TimelineEvent[] = hypotheses
    .filter((h) => h.status === 'proposed')
    .map((h) => ({
      id: `hypothesis-${h.id}`,
      kind: 'hypothesis',
      timestamp: h.firstProposedAt,
      title: t('Coach hypothesis'),
      body: shown(h.statement),
    }))
  const experimentEvents: TimelineEvent[] = experiments
    .filter((e) => e.status === 'suggested')
    .map((e) => ({
      id: `experiment-${e.id}`,
      kind: 'experiment',
      timestamp: e.createdAt,
      title: t('Suggested experiment'),
      body: shown(e.question),
    }))
  return [...questionEvents, ...hypothesisEvents, ...experimentEvents]
}
