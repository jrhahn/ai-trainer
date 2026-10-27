import type { ReactNode } from 'react'
import { usePlanTextPending } from '../hooks/usePlanTranslations'
import { useT } from '../i18n'

/** The grey line under a coach text whose German is still being written. Its
 *  English shows dimmed meanwhile, as the plan's texts do (ai-trainer-ops#258,
 *  ai-trainer-ops#271). */
export default function TranslationNote() {
  const t = useT()
  return <p className="text-xs text-gray-400">{t('Translation on its way …')}</p>
}

/** `children` dimmed, with the note under them, while any of `texts` is still
 *  on its way into the interface language. */
export function PendingTranslation({ texts, children }: { texts: (string | null | undefined)[]; children: ReactNode }) {
  const isPending = usePlanTextPending()
  const translating = texts.some(isPending)
  return (
    <>
      <div className={translating ? 'opacity-60' : undefined}>{children}</div>
      {translating && <TranslationNote />}
    </>
  )
}
