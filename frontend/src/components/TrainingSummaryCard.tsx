import { useAppStore } from '../store/useAppStore'
import { useT } from '../i18n'
import { card, cardTitle } from './ui'
import { splitTrainingSummary } from '../utils/trainingSummary'

/** The coach's summary of the recent training, under the status verdict
 *  (#418, #499, #623). Out of `DashboardPage` (ai-trainer-ops#274). */
export default function TrainingSummaryCard({ summaryLoading }: { summaryLoading: boolean }) {
  const t = useT()
  const riderAssessment = useAppStore((s) => s.riderAssessment)
  const loginSummary = riderAssessment?.loginSummary
    ? splitTrainingSummary(riderAssessment.loginSummary)
    : null

  // Training status is written by the coach, not by this component (#499).
  //
  // It used to be a local `done / due` ratio over the trailing week, which the
  // coach never saw — so when the athlete asked "why am I slightly behind?" the
  // coach had no idea the badge existed and invented a reason. That ratio was
  // also wrong on its own terms: it dropped two-a-days whose ride↔plan match came
  // back `ambiguous`, counted declining an explicitly optional session as a miss,
  // and ignored unplanned work entirely, so it could only ever punish.
  //
  // The backend status pipeline now owns the verdict and the wording, and feeds
  // the very same text into the coach's prompt. Rendering it here is a plain read.
  // From #623 the verdict was the colour of the summary card and its word was
  // for screen readers only. Since the owner's review of 2026-10-24 the card is
  // white like every other, so the word is back on screen, once.
  const trainingStatus = riderAssessment?.trainingStatusLabel
    ? {
        label: riderAssessment.trainingStatusLabel,
        title: riderAssessment.trainingStatusRationale,
      }
    : null

  if (!riderAssessment?.loginSummary && !summaryLoading && !trainingStatus) return null
  return (
    <div className={`${card} mb-3`} title={trainingStatus?.title}>
      <h3 className={cardTitle}>{t('Your recent training summary')}</h3>
      {/* The verdict, once, in words (#623; owner's review, 2026-10-24). */}
      {trainingStatus && <p className="text-sm font-medium text-gray-700">{trainingStatus.label}</p>}
      <div className="mt-2 text-sm leading-relaxed text-gray-700">
        {summaryLoading ? (
          <p className="text-gray-500">{t('Preparing your training summary…')}</p>
        ) : !riderAssessment?.loginSummary ? (
          // The card can now be opened by the status alone, before the
          // first summary has been written.
          <p className="text-gray-500">{t('Your first summary lands once a ride has been analysed.')}</p>
        ) : (
          <>
            {loginSummary?.intro && <p>{loginSummary.intro}</p>}
            {loginSummary?.bullets.length ? (
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {loginSummary.bullets.map((bullet, index) => (
                  <li key={`${bullet.label ?? 'summary'}-${index}`}>
                    {bullet.label && <span className="font-medium text-gray-900">{bullet.label}: </span>}
                    {bullet.text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="whitespace-pre-wrap">{riderAssessment!.loginSummary}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
