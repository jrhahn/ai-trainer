import type { SaveState } from '../hooks/useSaveStatus'
import { useT } from '../i18n'

/** "Saving…", "Saved" or what went wrong, next to the field it belongs to.
 *  Settings save themselves (ai-trainer-ops#93), so this is the only sign
 *  that they did. */
export default function SaveStatus({ state }: { state: SaveState }) {
  // The messages arrive in English and are translated here, where they show.
  const t = useT()
  if (state === 'idle') return null
  if (typeof state === 'object') {
    return (
      <p role="alert" className="mt-1 text-sm text-red-600">
        {t(state.error)}
      </p>
    )
  }
  return (
    <p role="status" className="mt-1 text-sm text-gray-500">
      {state === 'saving' ? t('Saving…') : t('Saved')}
    </p>
  )
}
