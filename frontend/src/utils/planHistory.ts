import type { PlanDayHistoryEntry } from '../services/user'
import type { TrainingDay } from '../types/domain'
import { english, translate, type Language, type Translate } from '../i18n'

/**
 * Athlete-friendly labels for the raw backend trigger keys (issue #357). The
 * backend records the trigger verbatim (see services/plan_pipeline.PLAN_SOURCES);
 * the frontend owns the human-readable naming.
 */
const SOURCE_LABELS: Record<string, string> = {
  user_edit: 'Your edit',
  coach_chat: 'Coach chat',
  next_ride: 'Next-ride recommendation',
  generate: 'Plan generation',
  adapt: 'Auto-adaptation',
  auto_adapt: 'Auto-adaptation',
  nightly_maintenance: 'Nightly tune-up',
  ride_review: 'Post-ride adaptation',
  // The coach's one look at the rides an intervals.icu connect brought in (ai-trainer-ops#306).
  history_review: 'Look at your imported rides',
  activity_import: 'Activity synced',
  manual_match: 'Match you confirmed',
  workout_log: 'Session you logged',
  heart_rate_caution: 'Heart-rate targets removed',
  // Well again: the sessions the illness turned into rest (ai-trainer-ops#232).
  illness_return: 'Sessions back after the illness',
}

/** Title-case an unknown snake_case trigger key as a readable fallback. */
function titleCase(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function sourceLabel(source: string, t: Translate = english): string {
  return t(SOURCE_LABELS[source] ?? titleCase(source))
}

/**
 * A short human diff of what a change did to a day. Compares the fields an
 * athlete cares about (type, title, duration). Handles create/remove where one
 * side is null.
 */
export function summarizeDayChange(
  oldDay: Partial<TrainingDay> | null,
  newDay: Partial<TrainingDay> | null,
  t: Translate = english,
  shown: (text: string) => string = (text) => text,
): string {
  // The raw type ("endurance", "rest") is its own key, so English reads as before.
  const kind = (day: Partial<TrainingDay>) => t(day.workoutType ?? 'workout')
  // A title is the plan's own English text: `shown` gives it in the interface
  // language (ai-trainer-ops#258).
  const titled = (day: Partial<TrainingDay>) => (day.title ? ` — ${shown(day.title)}` : '')
  if (!oldDay && newDay) {
    return t('Added {kind}{title}', { kind: kind(newDay), title: titled(newDay) })
  }
  if (oldDay && !newDay) {
    return t('Removed {kind}{title}', { kind: kind(oldDay), title: titled(oldDay) })
  }
  if (!oldDay || !newDay) {
    return t('No change')
  }

  const parts: string[] = []
  if (oldDay.workoutType !== newDay.workoutType) {
    parts.push(
      t('type {from} → {to}', {
        from: oldDay.workoutType ? t(oldDay.workoutType) : '—',
        to: newDay.workoutType ? t(newDay.workoutType) : '—',
      }),
    )
  }
  if (oldDay.title !== newDay.title) {
    parts.push(
      t('title “{from}” → “{to}”', {
        from: oldDay.title ? shown(oldDay.title) : '—',
        to: newDay.title ? shown(newDay.title) : '—',
      }),
    )
  }
  if (oldDay.durationMinutes !== newDay.durationMinutes) {
    parts.push(
      t('duration {from} → {to} min', {
        from: oldDay.durationMinutes ?? '—',
        to: newDay.durationMinutes ?? '—',
      }),
    )
  }
  if (!oldDay.completed && newDay.completed) {
    parts.push(t('marked complete'))
  }
  return parts.length > 0 ? parts.join(', ') : t('Minor adjustment')
}

/**
 * The athlete-facing sentence for one history entry. A blocked (`applied=false`)
 * automated attempt is framed as "wanted to … but kept your version".
 */
export function describeEntry(
  entry: PlanDayHistoryEntry,
  language: Language = 'en',
  shown: (text: string) => string = (text) => text,
): string {
  // One argument, so the words and the casing below cannot disagree.
  const t: Translate = (text, vars) => translate(language, text, vars)
  const label = sourceLabel(entry.source, t)
  const summary = summarizeDayChange(entry.oldDay, entry.newDay, t, shown)
  if (entry.applied) {
    return `${label}: ${summary}`
  }
  return t('{source} wanted to {change} but kept your version', {
    source: label,
    // Mid-sentence in English; German nouns keep their capital.
    change: language === 'en' ? summary.toLowerCase() : summary,
  })
}

/** One training day's worth of change entries, newest change first. */
export interface PlanHistoryDayGroup {
  date: string
  entries: PlanDayHistoryEntry[]
}

/**
 * Group history entries by their training-day `date` so the athlete sees the
 * changes made to each day together. Day groups are ordered by date descending
 * (latest training day first); within a day, entries keep newest-change-first
 * order (they arrive newest-first from the API).
 */
export function groupEntriesByDate(
  entries: PlanDayHistoryEntry[],
): PlanHistoryDayGroup[] {
  const byDate = new Map<string, PlanDayHistoryEntry[]>()
  for (const entry of entries) {
    const group = byDate.get(entry.date)
    if (group) {
      group.push(entry)
    } else {
      byDate.set(entry.date, [entry])
    }
  }
  return [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([date, group]) => ({ date, entries: group }))
}
