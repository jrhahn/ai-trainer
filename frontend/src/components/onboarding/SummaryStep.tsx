import { Loader2, Target } from 'lucide-react'
import { useT } from '../../i18n'
import { WEEKDAYS, type OnboardingForm } from '../../onboarding/form'

/** "Ready to Go!": the answers, and the only step that generates a plan
 *  (ai-trainer-ops#274: out of `OnboardingPage`). */
export default function SummaryStep({
  form,
  error,
  loading,
  onGenerate,
}: {
  form: OnboardingForm
  error: string
  loading: boolean
  onGenerate: () => void
}) {
  const t = useT()
  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Ready to Go!')}</h2>
      <p className="text-sm text-gray-500 mb-4">
        {t('Review your inputs and generate your first 14-day plan.')}
      </p>

      <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm mb-4">
        {(() => {
          const displayMaxHR = form.maxHeartRate ? t('{n} bpm', { n: form.maxHeartRate }) : null
          return (
            [
              // The stored values, translated as they are: 'cycling', 'general fitness'.
              [t('Sports'), form.sports.map((sport) => t(sport)).join(', ')],
              [t('Goal'), t(form.trainingGoal.replace('_', ' '))],
              [t('Training days'), WEEKDAYS.filter((d) => form.trainingDays.includes(d.value)).map((d) => t(d.label)).join(' ')],
              [t('Assessment'), t(form.assessmentMethod === 'intervals' ? 'intervals.icu (last 7 rides)' : form.assessmentMethod === 'strava' ? 'Strava (last 7 rides)' : 'Manual')],
              ...(form.raceDate ? [[t('Race Date'), form.raceDate]] : []),
              ...(form.fitnessLevel ? [[t('Fitness Level'), t(form.fitnessLevel)]] : []),
              ...(form.currentFTP ? [['FTP', `${form.currentFTP}W`]] : []),
              ...(displayMaxHR ? [[t('Max HR'), displayMaxHR]] : []),
            ] as [string, string][]
          ).map(([label, value]) => (
            <div key={label} className="flex justify-between">
              <span className="text-gray-500">{label}</span>
              <span className="font-medium text-gray-900">{value}</span>
            </div>
          ))
        })()}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">
          {t(error)}
        </div>
      )}

      <button
        onClick={onGenerate}
        disabled={loading}
        className="w-full bg-amber-500 text-white rounded-xl py-3 font-semibold flex items-center justify-center gap-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
      >
        {loading ? (
          <>
            <Loader2 size={18} className="animate-spin" />
            {t('Generating your plan...')}
          </>
        ) : (
          <>
            <Target size={18} />
            {t('Generate My 14-Day Training Plan')}
          </>
        )}
      </button>
    </div>
  )
}
