import { CheckCircle, Link, Loader2 } from 'lucide-react'
import IntervalsConnect from '../IntervalsConnect'
import StravaConnect from '../StravaConnect'
import SetupGuideLink from '../SetupGuideLink'
import { useLanguage, useT } from '../../i18n'
import { setupGuideUrl } from '../../utils/links'
import type { OnboardingForm } from '../../onboarding/form'

/** Step 4: how the coach learns the athlete's fitness, intervals.icu first
 *  (ai-trainer-ops#20, #274: out of `OnboardingPage`). */
export default function AssessmentStep({
  form,
  loading,
  error,
  offerStrava,
  stravaConnected,
  intervalsConnected,
  selectMethod,
  clearError,
  onBeforeStravaConnect,
}: {
  form: OnboardingForm
  loading: boolean
  error: string
  offerStrava: boolean
  stravaConnected: boolean
  intervalsConnected: boolean
  selectMethod: (method: OnboardingForm['assessmentMethod']) => void
  clearError: () => void
  onBeforeStravaConnect: () => Promise<void>
}) {
  const t = useT()
  const language = useLanguage()
  return (
    <div>
      {loading ? (
        <div className="text-center py-10">
          <Loader2 size={32} className="animate-spin text-amber-500 mx-auto mb-3" />
          <p className="font-semibold text-gray-900">{t('Analysing rides and generating your plan…')}</p>
          <p className="text-sm text-gray-500 mt-1">{t('This takes about 30 seconds.')}</p>
        </div>
      ) : (
        <>
          <h2 className="text-xl font-bold text-gray-900 mb-1">{t('How should we assess your fitness?')}</h2>
          <p className="text-sm text-gray-500 mb-4">
            {t('Connect intervals.icu for automatic analysis of your last 7 rides, or log your sessions yourself.')}
          </p>
          <div className="space-y-3">
            <button
              type="button"
              aria-pressed={form.assessmentMethod === 'intervals'}
              onClick={() => selectMethod('intervals')}
              className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                form.assessmentMethod === 'intervals'
                  ? 'border-amber-500 bg-amber-50'
                  : 'border-gray-200 hover:border-amber-300'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-sm text-gray-900">{t('Connect intervals.icu')}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {t('Recommended. Analyses your last 7 rides with your intervals.icu API key.')}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    {t('On Strava? Connect your Garmin, Wahoo or other device to intervals.icu (free) as well: rides that come only through Strava do not reach us.')}
                  </p>
                </div>
                {form.assessmentMethod === 'intervals' && (
                  <CheckCircle size={16} className="text-amber-500 shrink-0" />
                )}
              </div>
            </button>
            {/* Strava is offered only to an athlete already connected (ai-trainer-ops#39 Q6):
                new athletes come through intervals.icu, existing connections keep working. */}
            {offerStrava && (
            <button
              type="button"
              aria-pressed={form.assessmentMethod === 'strava'}
              onClick={() => selectMethod('strava')}
              className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                form.assessmentMethod === 'strava'
                  ? 'border-amber-500 bg-amber-50'
                  : 'border-gray-200 hover:border-amber-300'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-sm text-gray-900">{t('Connect with Strava')}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {t('Analyses your last 7 rides to estimate FTP and training zones.')}
                  </p>
                </div>
                {form.assessmentMethod === 'strava' && (
                  <CheckCircle size={16} className="text-amber-500 shrink-0" />
                )}
              </div>
            </button>
            )}
            <button
              type="button"
              aria-pressed={form.assessmentMethod === 'manual'}
              onClick={() => selectMethod('manual')}
              className={`w-full border-2 rounded-xl p-4 text-left transition-all ${
                form.assessmentMethod === 'manual'
                  ? 'border-amber-500 bg-amber-50'
                  : 'border-gray-200 hover:border-amber-300'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-sm text-gray-900">{t("I'll enter sessions myself")}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {t('The coach starts from your answers and learns from the sessions you log.')}
                  </p>
                </div>
                {form.assessmentMethod === 'manual' && (
                  <CheckCircle size={16} className="text-amber-500 shrink-0" />
                )}
              </div>
            </button>
          </div>
          {form.assessmentMethod === 'intervals' && (
            <div className="mt-4 space-y-3">
              <IntervalsConnect onChange={clearError} />
              {/* Where a friend gets stuck: the account, the device and the key, with screenshots (ai-trainer-ops#219). */}
              <SetupGuideLink href={setupGuideUrl(language)} label={t('Step-by-step guide with screenshots')} />
              {!intervalsConnected && (
                <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                  <Link size={14} className="shrink-0" />
                  {t('Connect intervals.icu to continue with automatic activity analysis.')}
                </div>
              )}
            </div>
          )}
          {form.assessmentMethod === 'strava' && (
            <div className="mt-4 space-y-3">
              <StravaConnect onBeforeConnect={onBeforeStravaConnect} />
              {!stravaConnected && (
                <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                  <Link size={14} className="shrink-0" />
                  {t('Connect Strava to continue with automatic activity analysis.')}
                </div>
              )}
            </div>
          )}
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mt-4">
              {t(error)}
            </div>
          )}
        </>
      )}
    </div>
  )
}
