import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { fetchPlanInputsChanged, generateTrainingPlan } from '../services/ai'
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

/** From this many rebuilds left today, the card says how many (ai-trainer-ops#175). */
export const SHOW_REBUILDS_LEFT_FROM = 3
/** The server's limit, for the sentence at the limit (``plan_inputs.DAILY_REBUILDS``). */
const DAILY_REBUILDS = 6

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
  const { authToken, activities, profile, raceEvents, setTrainingPlan } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      activities: s.rideMetricsHistory.length,
      profile: s.userProfile,
      // A race added from the chat or "+ Add race" is news (ai-trainer-ops#175).
      raceEvents: s.raceEvents,
      setTrainingPlan: s.setTrainingPlan,
    }))
  )
  const [reasons, setReasons] = useState<string[]>([])
  const [left, setLeft] = useState<number | null>(null)
  const account = profile?.email ?? ''
  const key = dismissedKey(account)
  // Read for the account on screen, not frozen at mount: the profile may load
  // after the card, or the account change under it (review of #979).
  const [dismissal, setDismissal] = useState<{ key: string; value: string | null }>(() => ({
    key,
    value: readDismissed(key),
  }))
  const dismissed = dismissal.key === key ? dismissal.value : readDismissed(key)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Asked again whenever something it compares may have moved here.
  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    fetchPlanInputsChanged(authToken)
      .then((result) => {
        if (cancelled) return
        setReasons(result.changed ? result.reasons : [])
        setLeft(typeof result.rebuildsLeft === 'number' ? result.rebuildsLeft : null)
      })
      .catch(() => {
        // Only an offer: without an answer there is nothing to offer.
      })
    return () => {
      cancelled = true
    }
  }, [authToken, activities, profile, raceEvents])

  const signature = reasons.join(',')
  // Without the account its "not now" cannot be read, so wait for it.
  if (!account || !reasons.length || dismissed === signature) return null

  const exhausted = left === 0

  const rebuild = async () => {
    if (!authToken || busy || exhausted) return
    setBusy(true)
    setError('')
    try {
      setTrainingPlan(await generateTrainingPlan(authToken))
      setReasons([])
      setLeft((now) => (now === null ? now : Math.max(0, now - 1)))
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
    setDismissal({ key, value: signature })
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
            disabled={busy || exhausted}
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
        {exhausted ? (
          <p className="text-xs text-gray-500">
            {t('You have rebuilt your plan {count} times today. You can rebuild it again tomorrow.', {
              count: DAILY_REBUILDS,
            })}
          </p>
        ) : (
          left !== null &&
          left <= SHOW_REBUILDS_LEFT_FROM && (
            <p className="text-xs text-gray-500">
              {left === 1
                ? t('You can rebuild your plan once more today.')
                : t('You can rebuild your plan {count} more times today.', { count: left })}
            </p>
          )
        )}
        {error && (
          <p role="alert" className="text-xs text-red-700">
            {t(error)}
          </p>
        )}
      </div>
    </section>
  )
}
