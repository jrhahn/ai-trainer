import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { card, iconButton } from './ui'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { PlanRebuild } from '../store/useAppStore'
import { fetchPlanInputsChanged } from '../services/ai'
import { useT } from '../i18n'
import { readLocal, writeLocal } from '../lib/storage'
import { NOTICE_MAX_AGE_MS, REASON_TEXT, seenKey } from '../utils/planRebuild'

/**
 * What the app rebuilt by itself, in one sentence (ai-trainer-ops#206).
 *
 * The plan follows the athlete's changes without a button now: right away for
 * what they changed themselves (`usePlanAutoRebuild`), at night for new rides
 * and answered questions. Not silently: this says which change rebuilt it,
 * until the athlete has seen it. It replaces the "Rebuild your plan" card of
 * #158, on the owner's word.
 */
export default function PlanRebuildNotice() {
  const t = useT()
  const { authToken, planRebuild, account } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, planRebuild: s.planRebuild, account: s.userProfile?.email ?? '' }))
  )
  const [fromServer, setFromServer] = useState<PlanRebuild | null>(null)
  // Dismissed in this visit: renders again, and holds where storage fails.
  const [dismissed, setDismissed] = useState<string | null>(null)
  // When the page opened: the age check needs a clock, and render must not read one.
  const [now] = useState(Date.now)
  const key = seenKey(account)

  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    fetchPlanInputsChanged(authToken)
      .then((result) => {
        if (!cancelled) setFromServer(result.lastRebuild ?? null)
      })
      .catch(() => {
        // Nothing to say without an answer.
      })
    return () => {
      cancelled = true
    }
  }, [authToken])

  // The rebuild just made here wins over the one the server last recorded.
  const rebuild = planRebuild ?? fromServer
  if (!account || !rebuild || !rebuild.reasons.length) return null
  if ((dismissed ?? readLocal(key)) === rebuild.at) return null
  if (now - new Date(rebuild.at).getTime() > NOTICE_MAX_AGE_MS) return null

  const dismiss = () => {
    // A private window: hidden for this visit only.
    writeLocal(key, rebuild.at)
    setDismissed(rebuild.at)
  }

  return (
    <section
      aria-label={t('Your plan is rebuilt')}
      className={`${card} flex items-center gap-3 text-sm text-gray-700`}
    >
      <p className="min-w-0 flex-1">
        {t('Your plan is rebuilt: {reasons}.', {
          reasons: rebuild.reasons.map((reason) => t(REASON_TEXT[reason] ?? reason)).join(', '),
        })}{' '}
        {t('Done and logged sessions stay as they are.')}
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t('Got it')}
        className={iconButton}
      >
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  )
}
