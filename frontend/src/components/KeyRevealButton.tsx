import { Eye } from 'lucide-react'
import { useT } from '../i18n'
import { revealWhileHeld } from '../utils/passwordChecks'

/** A saved key while hidden: no characters, and not its length (ai-trainer-ops#307). */
export const KEY_MASK = '••••••••••••••••'

/** The eye of a key: held, it shows the saved key's hint, or the key being
 *  typed, like the eye of a password field. The server sends no more of a
 *  saved key than its first and last four characters (ai-trainer-ops#307). */
export default function KeyRevealButton({
  shown,
  onShow,
  className,
}: {
  shown: boolean
  onShow: (shown: boolean) => void
  className: string
}) {
  const t = useT()
  return (
    <button
      type="button"
      aria-label={t('Hold to show key')}
      aria-pressed={shown}
      className={className}
      {...revealWhileHeld(onShow)}
    >
      <Eye size={18} aria-hidden="true" />
    </button>
  )
}
