import { useEffect, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import type { PlanRebuild } from '../store/useAppStore'
import { fetchPlanInputsChanged } from '../services/ai'
import { useT } from '../i18n'

/** How each reason the backend names reads to the athlete. */
export const REASON_TEXT: Record<string, string> = {
  training_days: 'your training days changed',
  sports: 'your sports changed',
  level: 'your level changed',
  ftp: 'your FTP changed',
  history: 'there are new rides on record',
  race: 'your race changed',
  assumptions: 'you answered my questions about you',
  hours: 'the hours you can train changed',
}

/** A rebuild older than this is old news on the dashboard. */
export const NOTICE_MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000

/** Where "seen" is kept: per browser and account, by the rebuild's time. */
export const seenKey = (account: string) => `plan-rebuild-seen:${account}`

function readSeen(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

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
  const [seen, setSeen] = useState<string | null>(null)
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

  useEffect(() => setSeen(readSeen(key)), [key])

  // The rebuild just made here wins over the one the server last recorded.
  const rebuild = planRebuild ?? fromServer
  if (!account || !rebuild || !rebuild.reasons.length) return null
  if (seen === rebuild.at) return null
  if (Date.now() - new Date(rebuild.at).getTime() > NOTICE_MAX_AGE_MS) return null

  const dismiss = () => {
    try {
      localStorage.setItem(key, rebuild.at)
    } catch {
      // A private window: hidden for this visit only.
    }
    setSeen(rebuild.at)
  }

  return (
    <section
      aria-label={t('Your plan is rebuilt')}
      className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-gray-700"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
        <RefreshCw size={16} aria-hidden="true" />
      </span>
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
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700"
      >
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  )
}
