import { RefreshCw } from 'lucide-react'
import { useT } from '../i18n'

/** The profile could not be loaded (ai-trainer-ops#118).
 *
 * A 502 or a dropped request on `/users/me`, as during a deploy, used to leave
 * an onboarded athlete "not onboarded" and route them to onboarding, where
 * clicking through saved its defaults over their race and generated a plan over
 * theirs. This page changes nothing and keeps the URL, so a retry lands where
 * they were going.
 */
export default function ProfileLoadFailed({
  onRetry,
  onSignOut,
}: {
  onRetry: () => void
  onSignOut: () => void
}) {
  const t = useT()
  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div role="alert" className="max-w-md w-full rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="text-lg font-semibold text-gray-900">{t('Your profile could not be loaded')}</h1>
        <p className="mt-2 text-sm text-gray-600">
          {t('Nothing was changed: your plan, races and settings are as you left them. This usually passes within a minute, for example during an update.')}
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600"
          >
            <RefreshCw size={16} aria-hidden="true" />
            {t('Try again')}
          </button>
          <button
            type="button"
            onClick={onSignOut}
            className="min-h-10 px-2 text-sm font-medium text-gray-600 hover:text-gray-900"
          >
            {t('Sign out')}
          </button>
        </div>
      </div>
    </main>
  )
}
