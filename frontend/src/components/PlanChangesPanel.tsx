import { useQuery } from '@tanstack/react-query'
import { History, ShieldCheck } from 'lucide-react'
import { fetchPlanHistory, fetchPlanHistoryStats } from '../services/user'
import { describeEntry, groupEntriesByDate, sourceLabel } from '../utils/planHistory'
import { parseLocalDate } from '../utils/workout'

interface PlanChangesPanelProps {
  authToken: string
}

/**
 * Read-only "recent plan changes" + override analytics for the athlete (#357).
 * The live plan still comes from the store; this only reflects the append-only
 * plan-day history. Renders nothing until there is at least one recorded change.
 */
export default function PlanChangesPanel({ authToken }: PlanChangesPanelProps) {
  const { data: stats } = useQuery({
    queryKey: ['planHistoryStats', authToken],
    queryFn: () => fetchPlanHistoryStats(authToken),
    enabled: !!authToken,
  })
  const { data: recent = [] } = useQuery({
    queryKey: ['planHistoryRecent', authToken],
    queryFn: () => fetchPlanHistory(authToken),
    enabled: !!authToken,
  })

  if (!stats || stats.total === 0) return null

  const topSources = Object.entries(stats.bySource)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
  const dayGroups = groupEntriesByDate(recent)

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
        <History size={16} className="text-amber-500" />
        Recent plan changes
      </h2>

      {/* Analytics summary */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-gray-50 rounded-xl p-3">
          <p className="text-xs text-gray-400">Total changes</p>
          <p className="text-lg font-bold text-gray-900">{stats.total}</p>
        </div>
        <div className="bg-gray-50 rounded-xl p-3">
          <p className="text-xs text-gray-400">Applied</p>
          <p className="text-lg font-bold text-gray-900">{stats.appliedCount}</p>
        </div>
        <div className="bg-gray-50 rounded-xl p-3">
          <p className="text-xs text-gray-400 flex items-center gap-1">
            <ShieldCheck size={11} /> Kept yours
          </p>
          <p className="text-lg font-bold text-gray-900">{stats.blockedCount}</p>
        </div>
      </div>

      {topSources.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {topSources.map(([source, count]) => (
            <span
              key={source}
              className="inline-block text-xs bg-amber-50 text-amber-700 rounded-full px-2.5 py-0.5"
            >
              {sourceLabel(source)} · {count}
            </span>
          ))}
        </div>
      )}

      {/* Timeline grouped by training day, scrollable so the full log is
          reachable — one section per calendar day (#357). */}
      <div className="space-y-4 max-h-72 overflow-y-auto pr-1">
        {dayGroups.map((group) => (
          <div key={group.date}>
            <p className="text-xs font-semibold text-gray-500 mb-1.5">
              {parseLocalDate(group.date).toLocaleDateString(undefined, {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
              })}
            </p>
            <ul className="space-y-2.5">
              {group.entries.map((entry) => (
                <li key={entry.id} className="flex items-start gap-2.5">
                  <span
                    className={`mt-1 shrink-0 w-2 h-2 rounded-full ${
                      entry.applied ? 'bg-amber-400' : 'bg-gray-300'
                    }`}
                  />
                  <p
                    className={`text-sm min-w-0 ${
                      entry.applied ? 'text-gray-700' : 'text-gray-500 italic'
                    }`}
                  >
                    {describeEntry(entry)}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}
