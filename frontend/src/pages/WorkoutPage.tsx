import { useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check, ChevronDown, ChevronUp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useAppStore } from '../store/useAppStore'
import { usePlan } from '../queries/plan'
import { useAuthToken } from '../hooks/useAuthToken'
import LogSessionDialog from '../components/LogSessionDialog'
import SessionHero from '../components/SessionHero'
import { effortLabel } from '../utils/effort'
import AIChat from '../components/AIChat'
import AmbiguousMatchResolver from '../components/AmbiguousMatchResolver'
import SessionPurposeQuestion from '../components/SessionPurposeQuestion'
import DayActivities from '../components/DayActivities'
import { useLogPlannedSession } from '../hooks/useLogPlannedSession'
import { fetchPlanHistory } from '../services/user'
import type { PlanDayHistoryEntry } from '../services/user'
import { parseLocalDate } from '../utils/workout'
import { describeEntry, sourceLabel } from '../utils/planHistory'
import { sessionKey, sessionLabel, sessionSlot, sessionsForDate } from '../utils/planSessions'
import { loggedSessionKeys } from '../utils/sessionCompletion'
import type { WorkoutFeedback, TrainingDay } from '../store/useAppStore'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { sportTypeText } from '../utils/activityType'
import { button, card, cardFrame, cardTitle, chip, dotted, note, textButton } from '../components/ui'

function ChangeHistorySection({
  expanded,
  onToggle,
  loading,
  entries,
}: {
  expanded: boolean
  onToggle: () => void
  loading: boolean
  entries: PlanDayHistoryEntry[]
}) {
  const t = useT()
  const language = useLanguage()
  const planText = usePlanText()
  return (
    <div className={cardFrame}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-4 text-left"
        aria-expanded={expanded}
      >
        <span className={cardTitle}>{t('Change history')}</span>
        {expanded ? (
          <ChevronUp size={16} className="text-gray-400" />
        ) : (
          <ChevronDown size={16} className="text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="px-5 pb-5">
          {loading ? (
            <p className={note(true)}>{t('Loading history…')}</p>
          ) : entries.length === 0 ? (
            <p className={note(true)}>{t('No recorded changes for this day yet.')}</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {entries.map((entry) => (
                <li key={entry.id} className="py-2">
                  <div className="min-w-0">
                    <p
                      className={`text-sm ${
                        entry.applied ? 'text-gray-800' : 'text-gray-500 italic'
                      }`}
                    >
                      {describeEntry(entry, language, planText)}
                    </p>
                    <p className="text-sm text-gray-500">
                      {!entry.applied && <span className={dotted}>{t('kept your version')}</span>}
                      <span className={dotted}>{sourceLabel(entry.source, t)}</span>
                      <span className={dotted}>{new Date(entry.recordedAt).toLocaleString(localeTag(language))}</span>
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Tab strip listing every session planned for the date (#496).
 *
 * Only rendered for a genuine two-a-day — a single-session day shows no strip at
 * all, so the page is unchanged for the common case.
 */
function SessionSwitcher({
  sessions,
  selected,
  onSelect,
}: {
  sessions: TrainingDay[]
  selected: TrainingDay
  onSelect: (session: TrainingDay) => void
}) {
  const t = useT()
  const planText = usePlanText()
  if (sessions.length <= 1) return null
  return (
    <div
      className="flex flex-wrap gap-2"
      role="tablist"
      aria-label={t('Sessions on this day')}
    >
      {sessions.map((session) => {
        const isSelected = sessionSlot(session) === sessionSlot(selected)
        const label = sessionLabel(session, sessions.length)
        return (
          <button
            key={sessionKey(session)}
            type="button"
            role="tab"
            aria-selected={isSelected}
            onClick={() => onSelect(session)}
            className={`${chip(isSelected)} flex items-center gap-1.5`}
          >
            <span className="text-gray-500">{label}</span>
            <span className="truncate max-w-[12rem]">{planText(session.title)}</span>
            {session.completed && <Check size={13} className="text-gray-500" aria-hidden="true" />}
          </button>
        )
      })}
    </div>
  )
}

export default function WorkoutPage() {
  const { date } = useParams<{ date: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const t = useT()
  const language = useLanguage()
  const authToken = useAuthToken()
  const trainingPlan = usePlan()
  const rideMetricsHistory = useAppStore((s) => s.rideMetricsHistory)
  // Rides on this date the coach has an open question about: which session it
  // was (#574, the calendar's amber dot leads here), or what it was at all when
  // the data could not say (#580). One list rather than two, because a ride can
  // carry both questions and listing it twice would read as two rides.
  const ridesNeedingAnswers = rideMetricsHistory.filter(
    (ride) =>
      ride.activityDate === date &&
      (ride.planMatchStatus === 'ambiguous' || ride.purposeQuestionOpen)
  )
  // Every session on this date, AM→PM. The `?slot=` query param picks which one
  // is open; without it the day's first session is shown, which is what a
  // single-session day (and every pre-#496 link) resolves to.
  const daySessions = sessionsForDate(trainingPlan, date)
  const requestedSlot = Number(searchParams.get('slot'))
  const day =
    daySessions.find((d) => sessionSlot(d) === requestedSlot) ?? daySessions[0]
  const selectSession = (session: TrainingDay) => {
    const slot = sessionSlot(session)
    setSearchParams(slot > 0 ? { slot: String(slot) } : {}, { replace: true })
  }
  const [showForm, setShowForm] = useState(false)
  // Auto-open history when the day isn't in the current plan window — for a
  // pruned past day the change log is the whole reason to open the page (#357).
  const [showHistory, setShowHistory] = useState(() => !day)

  const { data: planHistory = [], isLoading: historyLoading } = useQuery({
    queryKey: ['planHistory', date, authToken],
    queryFn: () => fetchPlanHistory(authToken, date!),
    enabled: showHistory && !!authToken && !!date,
  })

  const { logSession, isRating, rating } = useLogPlannedSession()

  if (!day) {
    // The day fell out of the rolling plan window (e.g. a completed past day),
    // but its change history still exists — surface it instead of a dead end (#357).
    const label = date
      ? parseLocalDate(date).toLocaleDateString(localeTag(language), {
          weekday: 'long',
          month: 'long',
          day: 'numeric',
        })
      : t('This day')
    return (
      <div className="space-y-6 max-w-2xl">
        <button
          onClick={() => navigate('/')}
          className={`flex items-center gap-1.5 ${textButton}`}
        >
          <ArrowLeft size={16} aria-hidden="true" />
          {t('Back to Dashboard')}
        </button>

        <div className={card}>
          <p className={cardTitle}>{label}</p>
          {/* Only a day the plan once held "is no longer part" of it. A day it
              never held — before the plan began, where the athlete enters past
              sessions by hand (ai-trainer-ops#207) — is not described as dropped. */}
          {!historyLoading && (
            <p className="text-sm text-gray-500 mt-1">
              {planHistory.length > 0
                ? t('This day is no longer part of your current plan, but its change history is still available.')
                : t('Your plan does not cover this day.')}
            </p>
          )}
        </div>

        {date && <DayActivities date={date} />}

        <ChangeHistorySection
          expanded={showHistory}
          onToggle={() => setShowHistory((v) => !v)}
          loading={historyLoading}
          entries={planHistory}
        />
      </div>
    )
  }

  // The form closes itself once saved; a failed save keeps it open (#127).
  const handleFeedback = (session: TrainingDay, feedback: WorkoutFeedback) =>
    logSession(session, feedback)

  const showLog = !day.completed && day.workoutType !== 'rest'
  // All of the day's sessions: on a two-a-day, one ride on the date must not
  // mark the open session done (utils/sessionCompletion).
  const doneKeys = loggedSessionKeys(daySessions, rideMetricsHistory, day.date)
  // A synced ride ticks the card, but only a log carries how it felt; the
  // button then says that rather than asking to log a session shown as done.
  const ridden = doneKeys.has(sessionKey(day))

  return (
    <div className="space-y-6 max-w-2xl lg:max-w-none">
      <button
        onClick={() => navigate('/')}
        className={`flex items-center gap-1.5 ${textButton}`}
      >
        <ArrowLeft size={16} aria-hidden="true" />
        {t('Back to Dashboard')}
      </button>

      <h1 className="text-2xl font-bold text-gray-900">
        {parseLocalDate(day.date).toLocaleDateString(localeTag(language), {
          weekday: 'long',
          year: 'numeric',
          month: 'long',
          day: 'numeric',
        })}
        {/* Names which of the day's sessions this is, so a two-a-day's
            header is never ambiguous (#496). */}
        {daySessions.length > 1 && ` · ${sessionLabel(day, daySessions.length)}`}
      </h1>

      <SessionSwitcher
        sessions={daySessions}
        selected={day}
        onSelect={selectSession}
      />

      {/* The session in the dashboard's card, so a day looks the same wherever
          it is opened (ai-trainer-ops#92). On a wide screen the chat sits beside
          it instead of below an empty half of the page. */}
      <div className="space-y-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0">
        <div className="min-w-0 space-y-6">
          <SessionHero
            sessions={[day]}
            date={day.date}
            doneKeys={doneKeys}
          />

          {/* Logging stays here rather than on the card: this page logs any
              day not yet done, a future one included, which the card does not.
              What was logged is a plain card, the coach's question in it plain
              text, and the button a grey one (owner's review, 2026-10-24). */}
          {day.completed && day.feedback && (
            <div className={`${card} text-sm text-gray-700`}>
              <h3 className={cardTitle}>{t('Your Workout Log')}</h3>
              <p className="mt-1">
                <span className={dotted}>{t('Duration: {minutes} min', { minutes: day.feedback.actualDurationMinutes })}</span>
                {day.feedback.averagePower && (
                  <span className={dotted}>{t('Avg Power: {watts}W', { watts: day.feedback.averagePower })}</span>
                )}
                {day.feedback.averageHeartRate && (
                  <span className={dotted}>{t('Avg HR: {bpm} bpm', { bpm: day.feedback.averageHeartRate })}</span>
                )}
                {day.feedback.peakPower && (
                  <span className={dotted}>{t('Peak Power: {watts}W', { watts: day.feedback.peakPower })}</span>
                )}
                {/* `effortLabel`, not a second copy of the scale: this page had its
                    own table without the strength wording, so a strength session
                    logged at 2 was "Controlled" in the form and "Moderate" here,
                    one screen later (ai-trainer-ops#51.5). */}
                {/* 0 is "not reported": a file the import brought in (ai-trainer-ops#311). */}
                {day.feedback.perceivedEffort > 0 && (
                  <span className={dotted}>
                    {t('Effort: {level}/5 ({label})', {
                      level: day.feedback.perceivedEffort,
                      label: t(effortLabel(day, day.feedback.perceivedEffort)),
                    })}
                  </span>
                )}
              </p>
              {day.feedback.notes && <p className="mt-1 text-gray-500">{day.feedback.notes}</p>}
              {/* Coach rating */}
              {isRating && <p className={`mt-3 ${note(true)}`}>{t('Coach is reviewing your session…')}</p>}
              {!isRating && day.coachFeedback && (
                <div className="mt-3 border-t border-gray-100 pt-3">
                  <p className="font-medium text-gray-900">{t("Coach's Feedback")}</p>
                  <p className="leading-relaxed">{day.coachFeedback}</p>
                  {rating?.followUpQuestion && (
                    <div className="mt-3">
                      <p className="font-medium text-gray-900">{t('Coach wants to know:')}</p>
                      <p className="leading-relaxed">{rating.followUpQuestion}</p>
                      {rating.suggestedFeedbackTags.length > 0 && (
                        <p className="mt-1 text-gray-500">
                          {rating.suggestedFeedbackTags.map((tag) => (
                            <span key={tag} className={`capitalize ${dotted}`}>
                              {tag.replace(/_/g, ' ')}
                            </span>
                          ))}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {showLog && (
            <button onClick={() => setShowForm(true)} className={button}>
              {ridden ? t('Log how it went') : t('Log Completed Workout')}
            </button>
          )}

          {ridesNeedingAnswers.length > 0 && (
            <div className={`${card} space-y-3`}>
              <h2 className={cardTitle}>
                {ridesNeedingAnswers.length > 1
                  ? t('Rides your coach has a question about on this day')
                  : t('A ride your coach has a question about on this day')}
              </h2>
              {ridesNeedingAnswers.map((ride) => (
                <div key={ride.stravaActivityId}>
                  <p className="truncate text-sm text-gray-900">
                    <span className={`font-medium ${dotted}`}>{ride.activityName ?? t('Activity')}</span>
                    <span className={`text-gray-500 ${dotted}`}>{t(sportTypeText(ride.sportType))}</span>
                  </p>
                  <AmbiguousMatchResolver ride={ride} />
                  <SessionPurposeQuestion ride={ride} />
                </div>
              ))}
            </div>
          )}

          {date && <DayActivities date={date} />}

          {/* Change history — "why did this workout change?" (#357) */}
          <ChangeHistorySection
            expanded={showHistory}
            onToggle={() => setShowHistory((v) => !v)}
            loading={historyLoading}
            entries={planHistory}
          />
        </div>

        <div className="min-w-0">
          <AIChat contextWorkout={day} />
        </div>
      </div>

      {showForm && (
        <LogSessionDialog open session={day} onLogPlanned={handleFeedback} onClose={() => setShowForm(false)} />
      )}
    </div>
  )
}
