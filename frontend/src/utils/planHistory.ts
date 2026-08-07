import type { PlanDayHistoryEntry } from '../services/user'
import type { TrainingDay } from '../store/useAppStore'

/**
 * Athlete-friendly labels for the raw backend trigger keys (issue #357). The
 * backend records the trigger verbatim (see services/plan_pipeline.PLAN_SOURCES);
 * the frontend owns the human-readable naming.
 */
export const SOURCE_LABELS: Record<string, string> = {
  user_edit: 'Your edit',
  coach_chat: 'Coach chat',
  next_ride: 'Next-ride recommendation',
  generate: 'Plan generation',
  adapt: 'Auto-adaptation',
  auto_adapt: 'Auto-adaptation',
  nightly_maintenance: 'Nightly tune-up',
  ride_review: 'Post-ride adaptation',
  activity_import: 'Activity synced',
  manual_match: 'Match you confirmed',
}

/** Title-case an unknown snake_case trigger key as a readable fallback. */
function titleCase(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? titleCase(source)
}

/**
 * A short human diff of what a change did to a day. Compares the fields an
 * athlete cares about (type, title, duration). Handles create/remove where one
 * side is null.
 */
export function summarizeDayChange(
  oldDay: Partial<TrainingDay> | null,
  newDay: Partial<TrainingDay> | null,
): string {
  if (!oldDay && newDay) {
    return `Added ${newDay.workoutType ?? 'workout'}${newDay.title ? ` — ${newDay.title}` : ''}`
  }
  if (oldDay && !newDay) {
    return `Removed ${oldDay.workoutType ?? 'workout'}${oldDay.title ? ` — ${oldDay.title}` : ''}`
  }
  if (!oldDay || !newDay) {
    return 'No change'
  }

  const parts: string[] = []
  if (oldDay.workoutType !== newDay.workoutType) {
    parts.push(`type ${oldDay.workoutType ?? '—'} → ${newDay.workoutType ?? '—'}`)
  }
  if (oldDay.title !== newDay.title) {
    parts.push(`title “${oldDay.title ?? '—'}” → “${newDay.title ?? '—'}”`)
  }
  if (oldDay.durationMinutes !== newDay.durationMinutes) {
    parts.push(`duration ${oldDay.durationMinutes ?? '—'} → ${newDay.durationMinutes ?? '—'} min`)
  }
  if (!oldDay.completed && newDay.completed) {
    parts.push('marked complete')
  }
  return parts.length > 0 ? parts.join(', ') : 'Minor adjustment'
}

/**
 * The athlete-facing sentence for one history entry. A blocked (`applied=false`)
 * automated attempt is framed as "wanted to … but kept your version".
 */
export function describeEntry(entry: PlanDayHistoryEntry): string {
  const label = sourceLabel(entry.source)
  const summary = summarizeDayChange(entry.oldDay, entry.newDay)
  if (entry.applied) {
    return `${label}: ${summary}`
  }
  return `${label} wanted to ${summary.toLowerCase()} but kept your version`
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
