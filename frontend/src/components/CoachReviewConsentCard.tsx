import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import { fetchCoachReviewConsent, updateCoachReviewConsent } from '../services/user'
import { localeTag, useLanguage, useT } from '../i18n'

export const COACH_REVIEW_CONSENT_QUERY_KEY = 'coach-review-consent'

/**
 * The consent to the weekly coach review (ai-trainer-ops#136). Shown only once
 * the server offers it, never ticked in advance and never part of sign-up.
 * Unticking withdraws at once.
 *
 * The checkbox sentence is part of the consent text: changing it (or its German
 * in de.ts) needs a new VERSION in backend/services/coach_review_consent.py,
 * which backend/tests/test_coach_review_consent_text.py checks.
 */
export default function CoachReviewConsentCard() {
  const t = useT()
  const language = useLanguage()
  const authToken = useAppStore((s) => s.authToken)
  const queryClient = useQueryClient()
  const [error, setError] = useState('')
  const { data } = useQuery({
    queryKey: [COACH_REVIEW_CONSENT_QUERY_KEY],
    queryFn: () => fetchCoachReviewConsent(authToken!),
    enabled: !!authToken,
  })
  const mutation = useMutation({
    mutationFn: (consented: boolean) => updateCoachReviewConsent(authToken!, consented),
    onSuccess: (value) => {
      setError('')
      queryClient.setQueryData([COACH_REVIEW_CONSENT_QUERY_KEY], value)
    },
    onError: () => setError(t('Could not save your choice. Please try again.')),
  })

  if (!data?.available) return null
  const since = data.consentedAt
    ? new Date(data.consentedAt).toLocaleDateString(localeTag(language), {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    : null

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-3">{t('Coach review')}</h3>
      <label className="flex items-start gap-3 text-sm text-gray-700">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0 accent-amber-600"
          checked={data.consented}
          disabled={mutation.isPending}
          onChange={(e) => mutation.mutate(e.target.checked)}
        />
        <span>
          {t(
            'I agree that my chat messages, plan changes and rides of a week go to Anthropic (USA), without my name and email, so the coach can be checked and improved. They include health data.'
          )}
        </span>
      </label>
      {since && (
        <p className="text-sm text-gray-500 mt-2">
          {t('Agreed on {date} (version {version}). Untick to withdraw.')
            .replace('{date}', since)
            .replace('{version}', data.version)}
        </p>
      )}
      <Link to="/privacy#coach-review" className="mt-2 inline-block text-sm font-medium text-amber-600 hover:underline">
        {t('What happens with it')}
      </Link>
      {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
    </div>
  )
}
