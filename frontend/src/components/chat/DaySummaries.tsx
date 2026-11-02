import type { ChatMessage } from '../../store/useAppStore'
import { localeTag, useLanguage, useT } from '../../i18n'
import { dayLabel } from '../../utils/coachTimeline'

// The coach's sentence per day its reply changed (ai-trainer-ops#266): beside the
// reply, never in it, so the reply stays the coach's own words and every changed
// day is still named. The sentence is the coach's, in the athlete's language; the
// day is the interface's.
export default function DaySummaries({ items }: { items: NonNullable<ChatMessage['daySummaries']> }) {
  const language = useLanguage()
  const t = useT()
  if (items.length === 0) return null
  return (
    <div>
      <p className="font-medium text-gray-700">{t('Changed in the plan')}</p>
      <ul className="mt-1 space-y-0.5 text-gray-600">
      {items.map((item) => (
        <li key={item.date}>
          <span className="font-medium text-gray-700">{dayLabel(item.date, localeTag(language))}:</span>{' '}
          {item.summary}
        </li>
      ))}
      </ul>
    </div>
  )
}
