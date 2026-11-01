import { CheckCircle, Dumbbell, Trophy } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { UserProfile } from '../../store/useAppStore'
import { useT } from '../../i18n'
import { SPORTS, WEEKDAYS, inWeekOrder, toggled, type OnboardingForm } from '../../onboarding/form'

// Onboarding's three questions (ai-trainer-ops#274: out of `OnboardingPage`).

type Update = (key: keyof OnboardingForm, value: OnboardingForm[keyof OnboardingForm]) => void

/** Step 1: the welcome, and which sports the plan should include. */
export function SportsStep({ athleteName, form, update }: { athleteName?: string; form: OnboardingForm; update: Update }) {
  const t = useT()
  return (
    <div className="text-center py-4">
      <div className="bg-amber-100 rounded-full w-16 h-16 flex items-center justify-center mx-auto mb-4">
        <Dumbbell size={32} className="text-amber-500" />
      </div>
      <h2 className="text-2xl font-bold text-gray-900 mb-2">
        {/* No waving hand (ai-trainer-ops#51.7) — and this heading
            already has a 32 px dumbbell directly above it. */}
        {athleteName ? t('Welcome, {name}!', { name: athleteName }) : t('Welcome, athlete!')}
      </h2>
      {/* The owner's words (ai-trainer-ops#203), in place of the box
          with three ticks that "looked like AI". What they promise
          holds: every answer can be changed in Settings and in the
          chat (#202). */}
      <p className="text-gray-500 mb-6 text-sm leading-relaxed">
        {t('We have 4 short questions to get you started. No worries if you are unsure: talk to your coach later to adapt it, or change it in your settings.')}
      </p>
      <p id="onboarding-sports-question" className="mb-3 text-sm font-semibold text-gray-800">
        {t('Which sports should your plan include?')}
      </p>
      <div className="flex flex-wrap justify-center gap-2" role="group" aria-labelledby="onboarding-sports-question">
        {SPORTS.map(({ value, label }) => {
          const on = form.sports.includes(value)
          return (
            <button
              key={value}
              type="button"
              aria-pressed={on}
              onClick={() => update('sports', toggled(form.sports, value))}
              className={`rounded-full border-2 px-4 py-1.5 text-sm font-medium transition-all ${
              on ? 'border-amber-500 bg-amber-50 text-amber-800' : 'border-gray-200 text-gray-600 hover:border-amber-300'
            }`}
            >
              {t(label)}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** Step 2: a race to prepare for, or general fitness. */
export function GoalStep({
  form,
  update,
  selectTrainingGoal,
}: {
  form: OnboardingForm
  update: Update
  selectTrainingGoal: (goal: OnboardingForm['trainingGoal']) => void
}) {
  const t = useT()
  const goals: {
    value: UserProfile['trainingGoal']
    label: string
    desc: string
    Icon: LucideIcon
  }[] = [
    { value: 'race', label: t('Race Prep'), desc: t('Be ready for a specific race'), Icon: Trophy },
    {
      value: 'general_fitness',
      label: t('General Fitness'),
      desc: t('Stay fit and healthy'),
      Icon: Dumbbell,
    },
  ]

  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Are you preparing for a race?')}</h2>
      <p className="text-sm text-gray-500 mb-4">{t('Race prep adds a target date; otherwise your plan starts as general fitness.')}</p>
      <div className="space-y-2">
        {goals.map((g) => (
          <button
            key={g.value}
            type="button"
            // The tick beside the chosen option is the only thing that
            // said which one it was, so the choice was invisible to a
            // screen reader and unassertable in a test — the same
            // colour-only problem #51.7 fixed on the leg-freshness
            // indicator. The effort scale already does this
            // (ai-trainer-ops#46).
            aria-pressed={form.trainingGoal === g.value}
            onClick={() => selectTrainingGoal(g.value)}
            className={`w-full flex items-center gap-3 p-3 rounded-xl border-2 text-left transition-all ${
              form.trainingGoal === g.value
                ? 'border-amber-500 bg-amber-50'
                : 'border-gray-200 hover:border-amber-300'
            }`}
          >
            {/* `aria-hidden`: the goal's name is right beside it. */}
            <g.Icon size={24} className="shrink-0 text-amber-500" aria-hidden="true" />
            <div>
              <div className="font-semibold text-sm text-gray-900">{g.label}</div>
              <div className="text-xs text-gray-500">{g.desc}</div>
            </div>
            {form.trainingGoal === g.value && (
              <CheckCircle size={16} className="ml-auto text-amber-500" />
            )}
          </button>
        ))}
      </div>
      {form.trainingGoal === 'race' && (
        <div className="mt-4 space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Race Date')}</label>
            <input
              type="date"
              value={form.raceDate}
              onChange={(e) => update('raceDate', e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-base sm:text-sm focus:ring-amber-500 focus:border-amber-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Race Description')}</label>
            <input
              type="text"
              value={form.raceDescription}
              onChange={(e) => update('raceDescription', e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-base sm:text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('e.g. 80km gran fondo with 2000m climbing')}
            />
          </div>
        </div>
      )}
    </div>
  )
}

/** Step 3: the days, and the optional level, FTP and max heart rate (ai-trainer-ops#43). */
export function DaysStep({ form, update }: { form: OnboardingForm; update: Update }) {
  const t = useT()
  const levels: { value: UserProfile['fitnessLevel']; label: string; desc: string }[] = [
    { value: 'beginner', label: t('Beginner'), desc: t('Training < 1 year') },
    { value: 'intermediate', label: t('Intermediate'), desc: t('1-3 years experience') },
    { value: 'advanced', label: t('Advanced'), desc: t('3+ years, racing or structured training') },
  ]

  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900 mb-1">{t('When can you train?')}</h2>
      <p className="text-sm text-gray-500 mb-4">{t('A starting point, not a fixed rule: the coach plans on these days and learns from your training how much time you have. Tell the coach if a day generally works better for you than another, for example Monday rather than Tuesday, or if a week looks different.')}</p>
      <div className="space-y-5">
        <div>
          <p className="block text-sm font-medium text-gray-700 mb-2">{t('Days you can train')}</p>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('Days you can train')}>
            {WEEKDAYS.map(({ value, label }) => {
              const on = form.trainingDays.includes(value)
              return (
                <button
                  key={value}
                  type="button"
                  aria-pressed={on}
                  onClick={() => update('trainingDays', inWeekOrder(toggled(form.trainingDays, value)))}
                  className={`w-11 rounded-lg border-2 py-1.5 text-sm font-medium transition-all ${
                    on ? 'border-amber-500 bg-amber-50 text-amber-800' : 'border-gray-200 text-gray-500 hover:border-amber-300'
                  }`}
                >
                  {t(label)}
                </button>
              )
            })}
          </div>
          {form.trainingDays.length === 0 && (
            <p className="mt-1 text-xs text-red-600">{t('Pick at least one day.')}</p>
          )}
        </div>
        <details className="rounded-lg border border-gray-200 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-gray-700">
            {t('Optional: level, FTP, max heart rate')}
          </summary>
          <p className="mt-2 mb-4 text-xs text-gray-500">
            {t('Skip these if you are not sure. The coach estimates them from your first sessions.')}
          </p>
          <div className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">{t('Fitness Level')}</label>
          <div className="space-y-2">
            {levels.map((l) => (
              <button
                key={l.value}
                type="button"
                aria-pressed={form.fitnessLevel === l.value}
                onClick={() => update('fitnessLevel', l.value)}
                className={`w-full flex items-center justify-between px-4 py-3 rounded-lg border-2 transition-all ${
                  form.fitnessLevel === l.value
                    ? 'border-amber-500 bg-amber-50'
                    : 'border-gray-200 hover:border-amber-300'
                }`}
              >
                <div className="text-left">
                  <div className="font-semibold text-sm">{l.label}</div>
                  <div className="text-xs text-gray-500">{l.desc}</div>
                </div>
                {form.fitnessLevel === l.value && (
                  <CheckCircle size={16} className="text-amber-500" />
                )}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('Current FTP (watts)')} <span className="text-gray-400 font-normal">optional</span>
            </label>
            <input
              type="number"
              value={form.currentFTP}
              onChange={(e) => update('currentFTP', e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-base sm:text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('e.g. {example}', { example: 250 })}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('Max Heart Rate (bpm)')} <span className="text-gray-400 font-normal">optional</span>
            </label>
            <input
              type="number"
              value={form.maxHeartRate}
              onChange={(e) => update('maxHeartRate', e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-base sm:text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('e.g. {example}', { example: 185 })}
            />

          </div>
        </div>
          </div>
        </details>
      </div>
    </div>
  )
}
