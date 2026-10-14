import { Code } from 'lucide-react'
import type { ChatMessage } from '../store/useAppStore'
import { GITHUB_URL } from '../utils/links'

// What the coach was given for this reply (ai-trainer-ops#17, #81 Q24): the
// deterministic blocks of its prompt, each linked to the function that computed
// it at the deployed commit. The reply itself is prose and stays unlinked.
export default function CoachSaw({ items }: { items: NonNullable<ChatMessage['coachSaw']> }) {
  if (items.length === 0) return null
  return (
    <details className="mt-2 border-t border-gray-200 pt-2">
      <summary className="flex cursor-pointer items-center gap-1 text-xs font-semibold text-gray-500 hover:text-amber-600">
        <Code size={11} />
        What the coach saw
      </summary>
      <ul className="mt-1 space-y-2">
        {items.map((item) => (
          <li key={item.key} className="text-xs text-gray-600">
            <p className="font-medium text-gray-700">
              {item.title}{' '}
              <a
                href={`${GITHUB_URL}/blob/${item.source.ref}/${item.source.path}#L${item.source.line}`}
                target="_blank"
                rel="noreferrer"
                className="font-normal text-amber-700 underline"
              >
                {item.source.symbol}
              </a>
            </p>
            <p className="mt-0.5 whitespace-pre-wrap">{item.text}</p>
          </li>
        ))}
      </ul>
    </details>
  )
}
