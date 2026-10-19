import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { fetchPlanInputsChanged, generateTrainingPlan } from '../services/ai'
import { useT } from '../i18n'

/** How each reason the backend names reads to the athlete. */
export const REASON_TEXT: Record<string, string> = {
  training_days: 'your training days changed',
  ftp: 'your FTP changed',
  history: 'there are new rides on record',
  race: 'your race changed',
  assumptions: 'you answered my questions about you',
  hours: 'the hours you can train changed',
}

/** Where a dismissal is kept: per browser and account, until the reasons
 * change. Keyed by the account, so on a shared browser one athlete's "not
 * now" does not hide another's offer. */
export const dismissedKey = (account: string) => `rebuild-plan-dismissed:${account}`

function readDismissed(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** Offer to rebuild the plan once the coach knows something it was not built
 * from (ai-trainer-ops#158).
 *
 * A full plan was generated at onboarding and at the first sync, and never
 * again: an imported history, an answered question or new training days
 * reshaped nothing unless the chat proposed it. The backend compares what the
 * plan was built from with what is known now; this card names what moved and
 * leaves the rebuild to the athlete. Done and logged days survive a rebuild.
 */
export default function RebuildPlanCard() {
  const t = useT()
  const { authToken, activities, profile, setTrainingPlan } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      activities: s.rideMetricsHistory.length,
      profile: s.userProfile,
      setTrainingPlan: s.setTrainingPlan,
    }))
  )
  const [reasons, setReasons] = useState<string[]>([])
  const key = dismissedKey(profile?.email ?? '')
  const [dismissed, setDismissed] = useState<string | null>(() => readDismissed(key))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Asked again whenever something it compares may have moved here.
  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    fetchPlanInputsChanged(authToken)
      .then((result) => {
        if (!cancelled) setReasons(result.changed ? result.reasons : [])
      })
      .catch(() => {
        // Only an offer: without an answer there is nothing to offer.
      })
    return () => {
      cancelled = true
    }
  }, [authToken, activities, profile])

  const signature = reasons.join(',')
  if (!reasons.length || dismissed === signature) return null

  const rebuild = async () => {
    if (!authToken || busy) return
    setBusy(true)
    setError('')
    try {
      setTrainingPlan(await generateTrainingPlan(authToken))
      setReasons([])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not rebuild the plan.')
    } finally {
      setBusy(false)
    }
  }

  const dismiss = () => {
    try {
      localStorage.setItem(key, signature)
    } catch {
      // A private window: hidden for this visit only.
    }
    setDismissed(signature)
  }

  return (
    <section
      aria-label={t('Rebuild your plan')}
      className="flex gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-gray-700"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
        <RefreshCw size={16} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <p>
          {t('Your coach knows more about you now: {reasons}.', {
            reasons: reasons.map((reason) => t(REASON_TEXT[reason] ?? reason)).join(', '),
          })}{' '}
          {t('Your plan was built before that. Done and logged sessions stay as they are.')}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void rebuild()}
            disabled={busy}
            className="rounded-lg bg-amber-500 px-3 py-1.5 font-medium text-white hover:bg-amber-600 disabled:opacity-60"
          >
            {busy ? t('Rebuilding…') : t('Rebuild my plan')}
          </button>
          <button
            type="button"
            onClick={dismiss}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 font-medium text-gray-500 hover:text-gray-700"
          >
            {t('Not now')}
          </button>
        </div>
        {error && (
          <p role="alert" className="text-xs text-red-700">
            {t(error)}
          </p>
        )}
      </div>
    </section>
  )
}
