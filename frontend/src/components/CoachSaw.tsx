import { useAppStore, type ChatMessage } from '../store/useAppStore'
import { GITHUB_URL } from '../utils/links'
import { useT } from '../i18n'
import { textButton } from './ui'

// What the coach was given for this reply (ai-trainer-ops#17, #81 Q24): the
// deterministic blocks of its prompt, each linked to the function that computed
// it at the deployed commit. The reply itself is prose and stays unlinked.
//
// Expert mode only (review of 2026-11-01, ai-trainer-ops#338): the blocks are
// English prompt text with instructions to the model and code links. Under
// every reply, a hobby rider read the inside of the machine, not a coach; #215
// and #217 took the same kind of text out of Settings and the timeline.
export default function CoachSaw({ items }: { items: NonNullable<ChatMessage['coachSaw']> }) {
  const t = useT()
  const isExpertMode = useAppStore((state) => state.isExpertMode)
  if (!isExpertMode || items.length === 0) return null
  return (
    <details className="mt-2 border-t border-gray-100 pt-2">
      <summary className={`cursor-pointer ${textButton}`}>{t('What the coach saw')}</summary>
      <ul className="mt-1 space-y-2">
        {items.map((item) => (
          <li key={item.key} className="text-gray-600">
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
