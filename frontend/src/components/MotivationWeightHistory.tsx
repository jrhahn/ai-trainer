import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, History, Loader2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import {
  fetchMotivationWeightHistory,
  type MotivationWeightEvent,
} from '../services/user'

export const WEIGHT_HISTORY_QUERY_KEY = 'motivation-weight-history'

/**
 * Why the weight balance looks the way it does (#566).
 *
 * The balance above decides which session the coach recommends, and it moves on
 * its own as the athlete's riding changes. Without this, "why is this different
 * from last week?" had no answer anywhere in the product — the only trace was a
 * backend log line.
 *
 * Collapsed by default and fetched only when opened: this is a question the
 * athlete asks occasionally, not something the settings page owes them on every
 * load.
 */
export default function MotivationWeightHistory({
  componentLabels,
}: {
  componentLabels: Record<string, string>
}) {
  const authToken = useAppStore((s) => s.authToken)
  const [expanded, setExpanded] = useState(false)

  const { data: events = [], isLoading, isError } = useQuery({
    queryKey: [WEIGHT_HISTORY_QUERY_KEY, authToken],
    queryFn: () => fetchMotivationWeightHistory(authToken!),
    enabled: expanded && !!authToken,
  })

  const label = (component: string) => componentLabels[component] ?? component

  return (
    <div className="border-t border-gray-100 pt-4">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="flex items-center gap-1.5 text-sm font-semibold text-gray-700">
          <History size={14} className="text-amber-500" />
          What moved this
        </span>
        {expanded ? (
          <ChevronUp size={14} className="text-gray-400" />
        ) : (
          <ChevronDown size={14} className="text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="mt-3">
          {isLoading ? (
            <p className="flex items-center gap-1.5 text-xs text-gray-400">
              <Loader2 size={12} className="animate-spin" />
              Loading…
            </p>
          ) : isError ? (
            <p className="text-xs text-red-600">Could not load the history.</p>
          ) : events.length === 0 ? (
            <p className="text-xs text-gray-400">
              Nothing has moved your balance yet. It changes when you edit it, or
              when several weeks of riding argue for something different.
            </p>
          ) : (
            <ul className="space-y-3">
              {events.map((event) => (
                <WeightEventRow key={event.id} event={event} label={label} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function WeightEventRow({
  event,
  label,
}: {
  event: MotivationWeightEvent
  label: (component: string) => string
}) {
  const moved = Object.entries(event.deltas)
  const byUser = event.source === 'user_set'
  // A pinned component that a rule argued for is the most confusing thing an
  // athlete can see here — the rule fired and nothing happened. Say so.
  const blocked = (event.rules ?? [])
    .flatMap((rule) => Object.keys(rule.effects))
    .filter((component) => event.pinned.includes(component))

  return (
    <li className="text-xs">
      <p className="flex flex-wrap items-baseline gap-x-2 text-gray-700">
        <span className="font-semibold">
          {byUser ? 'You changed this' : 'Learned from your riding'}
        </span>
        {event.recordedAt && (
          <span className="text-gray-400">
            {new Date(event.recordedAt).toLocaleDateString()}
          </span>
        )}
      </p>

      <p className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-gray-600">
        {moved.map(([component, delta]) => (
          <span key={component} className="tabular-nums">
            {label(component)}{' '}
            <span className={delta > 0 ? 'text-emerald-600' : 'text-orange-600'}>
              {delta > 0 ? '+' : '−'}
              {Math.abs(Math.round(delta * 100))}%
            </span>
          </span>
        ))}
      </p>

      {event.rules && event.rules.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {event.rules.map((rule) => (
            <li key={rule.rule} className="text-[11px] text-gray-400">
              because you {rule.signal}
            </li>
          ))}
        </ul>
      )}

      {[...new Set(blocked)].map((component) => (
        <p key={component} className="mt-0.5 text-[11px] text-amber-700">
          {label(component)} stayed where you pinned it.
        </p>
      ))}
    </li>
  )
}
