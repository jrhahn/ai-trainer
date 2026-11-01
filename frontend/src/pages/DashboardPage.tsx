import { lazy, Suspense, useEffect, useState } from 'react'
import { subDays } from 'date-fns'
import { formatLongDate, localeTag, useLanguage, useT } from '../i18n'
import { Flag, Plus } from 'lucide-react'
import { button, card, cardTitle, note } from '../components/ui'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { useProfile } from '../queries/profile'
import { usePlan } from '../queries/plan'
import type { RideMetricPoint, TrainingDay } from '../store/useAppStore'
import AIChat from '../components/AIChat'
import PlanChangesPanel from '../components/PlanChangesPanel'
import TrainingCalendar from '../components/TrainingCalendar'
import AthletePerformanceModelCard from '../components/AthletePerformanceModelCard'
import ActivityAnalysisProgress from '../components/ActivityAnalysisProgress'
import ActivityRow from '../components/ActivityRow'
import SessionHero from '../components/SessionHero'
import DayBoardWhy from '../components/DayBoardWhy'
import TrainingSummaryCard from '../components/TrainingSummaryCard'
import { sessionsForDate } from '../utils/planSessions'
import { loggedSessionKeys, nextUnfinishedSession } from '../utils/sessionCompletion'
import { dedupeRideMetricsByActivity, rideActivityKey } from '../utils/rideDedupe'
import SeasonCountdown from '../components/SeasonCountdown'
import ColdStartNote from '../components/ColdStartNote'
import PlanRebuildNotice from '../components/PlanRebuildNotice'
import WeekStrip from '../components/WeekStrip'
import SeasonPlan from '../components/SeasonPlan'
import Modal from '../components/Modal'
import LogSessionDialog, { type EditedActivity } from '../components/LogSessionDialog'
import { useEnteredActivityActions } from '../hooks/useEnteredActivityActions'
import AddRaceDialog from '../components/AddRaceDialog'
import { useLogPlannedSession } from '../hooks/useLogPlannedSession'
import { useStravaSync } from '../hooks/useStravaSync'
import { useTrainingSummary } from '../hooks/useTrainingSummary'
import PredictionScoreboard from '../components/PredictionScoreboard'
import { useScoredPredictions } from '../hooks/useScoredPredictions'
import { formatLocalDate, parseLocalDate } from '../utils/workout'
import { accountKey, readLocal, writeLocal } from '../lib/storage'

// Expert mode only, so most athletes never need its code (ai-trainer-ops#275).
const ProgressionChart = lazy(() => import('../components/ProgressionChart'))

// Per account (ai-trainer-ops#269): two friends on one browser shared it, and
// the second saw "new" badges from the first one's last login.
const PREV_LOGIN_KEY = 'ai_trainer_previous_login'

export default function DashboardPage() {
  const t = useT()
  const language = useLanguage()
  const { hasScored: hasScoredPredictions } = useScoredPredictions()
  const trainingPlan = usePlan()
  const userProfile = useProfile()
  const { authToken, isExpertMode, rideMetricsHistory } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      isExpertMode: s.isExpertMode,
      rideMetricsHistory: s.rideMetricsHistory,
    }))
  )

  const [calendarOpen, setCalendarOpen] = useState(false)
  const [addActivityOpen, setAddActivityOpen] = useState(false)
  const [addRaceOpen, setAddRaceOpen] = useState(false)
  const [editingActivity, setEditingActivity] = useState<EditedActivity | null>(null)
  // The session being logged from the card, if any (ai-trainer-ops#52).
  const [loggingSession, setLoggingSession] = useState<TrainingDay | null>(null)
  const { logSession } = useLogPlannedSession()
  // Which day the hero is showing.  Owned here rather than in the strip because
  // the hero is the thing that renders it, and the two are siblings (#634).
  // Lazy, not `today`: that is declared further down this component.
  const [selectedDate, setSelectedDate] = useState(() => formatLocalDate(new Date()))

  // The last login, read once per account while rendering; the effect then
  // stamps this one. No state set in the effect (ai-trainer-ops#286).
  const account = userProfile?.email ?? ''
  const [lastLogin, setLastLogin] = useState<{ account: string; date: string | null } | null>(null)
  if (account && lastLogin?.account !== account) {
    const stored = readLocal(accountKey(PREV_LOGIN_KEY, account))
    setLastLogin({ account, date: stored ? formatLocalDate(new Date(stored)) : null })
  }
  const prevLoginDate = lastLogin?.account === account ? lastLogin.date : null
  useEffect(() => {
    if (account) writeLocal(accountKey(PREV_LOGIN_KEY, account), new Date().toISOString())
  }, [account])

  // keep sync running so analysis status updates remain active
  const { analysisStatus, analysisError } = useStravaSync()

  const firstName = userProfile?.name?.trim().split(/\s+/)[0] ?? ''

  const greeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }

  const today = formatLocalDate(new Date())

  const consumedTokens = userProfile?.consumedTokens ?? 0

  // Calendar days back from `today`, so render reads the clock once
  // (ai-trainer-ops#286).
  const threeDaysAgo = formatLocalDate(subDays(parseLocalDate(today), 3))
  const sevenDaysAgo = formatLocalDate(subDays(parseLocalDate(today), 7))

  // Extend the window to 7 days if there are new (since last login) activities
  // that fall between 3 and 7 days ago
  const hasNewBeyond3Days =
    prevLoginDate !== null &&
    rideMetricsHistory.some(
      (r) =>
        r.activityDate >= sevenDaysAgo &&
        r.activityDate < threeDaysAgo &&
        r.activityDate >= prevLoginDate!
    )
  const recentWindow = hasNewBeyond3Days ? sevenDaysAgo : threeDaysAgo

  const recentRides = dedupeRideMetricsByActivity(rideMetricsHistory
    .filter((r) => r.activityDate >= recentWindow && r.activityDate <= today)
    .sort((a, b) => {
      const dateOrder = b.activityDate.localeCompare(a.activityDate)
      if (dateOrder !== 0) return dateOrder
      const startOrder = (b.activityStartDatetime ?? '').localeCompare(a.activityStartDatetime ?? '')
      if (startOrder !== 0) return startOrder
      return b.stravaActivityId - a.stravaActivityId
    }))

  const isNew = (r: RideMetricPoint): boolean => {
    if (!prevLoginDate) return false
    return r.activityDate >= prevLoginDate
  }
  // Every session on the selected date, not `plan.find(d => d.date === …)` —
  // that keeps only one, which is the two-a-day bug #496 fixed and the redesign
  // reintroduced (#645).
  const selectedSessions = sessionsForDate(trainingPlan, selectedDate)
  const selectedDoneKeys = loggedSessionKeys(selectedSessions, recentRides, selectedDate)
  // The coach gets the next session still to be ridden, so a two-a-day stops
  // handing it the morning session all afternoon.
  const coachContextWorkout = nextUnfinishedSession(
    sessionsForDate(trainingPlan, today),
    loggedSessionKeys(sessionsForDate(trainingPlan, today), recentRides, today)
  )

  const latestRecentRide = recentRides[0] ?? null

  // Edit and delete of an entered activity, shared with the day page (#207).
  const enteredActions = useEnteredActivityActions()
  const editEnteredActivity = async (ride: RideMetricPoint) => {
    const edited = await enteredActions.open(ride)
    if (edited) setEditingActivity(edited)
  }
  const removeEnteredActivity = async (ride: RideMetricPoint) => {
    await enteredActions.remove(ride)
  }

  const summaryLoading = useTrainingSummary(latestRecentRide)

  const renderRide = (ride: RideMetricPoint) => (
    <ActivityRow
      key={rideActivityKey(ride)}
      ride={ride}
      isNew={isNew(ride)}
      onEdit={editEnteredActivity}
      onRemove={removeEnteredActivity}
    />
  )

  // A ride the coach needs an answer about stays on the first screen; the rest
  // of the history is one tap away (ai-trainer-ops#68).
  // A ride stays here for the rest of the visit once it was here: answering it
  // must not move the row, or the question's own "thanks" would unmount with
  // it before the athlete sees it. Kept in state and grown while rendering, not
  // in a ref read during render (ai-trainer-ops#286).
  const [keysAwaitingAnswer, setKeysAwaitingAnswer] = useState<ReadonlySet<string>>(() => new Set())
  const newlyAwaiting = recentRides
    .filter((ride) => ride.planMatchStatus === 'ambiguous' || Boolean(ride.purposeQuestionOpen))
    .map(rideActivityKey)
    .filter((key) => !keysAwaitingAnswer.has(key))
  if (newlyAwaiting.length > 0) setKeysAwaitingAnswer(new Set([...keysAwaitingAnswer, ...newlyAwaiting]))
  const needsAnswer = (ride: RideMetricPoint) => {
    const key = rideActivityKey(ride)
    return keysAwaitingAnswer.has(key) || newlyAwaiting.includes(key)
  }
  const ridesAwaitingAnswer = recentRides.filter(needsAnswer)
  const otherRides = recentRides.filter((ride) => !needsAnswer(ride))

  return (
    <div className="space-y-5">
      {/* Header. Adding an activity sits here, beside the greeting, instead of
          taking a row of its own above the week (ai-trainer-ops#68). */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-900">
            {/* No name means no comma: "Good morning!", not "Good morning, !" (ai-trainer-ops#40). */}
            {/* No waving hand (ai-trainer-ops#51.7). It was one of the emoji
                the issue names, and there is no lucide icon that reads as a
                greeting — `Hand` is a hand, not a wave. A greeting by name needs
                no decoration, so it is dropped rather than translated. */}
            {firstName ? `${t(greeting())}, ${firstName}!` : `${t(greeting())}!`}
          </h1>
          <p className="text-gray-500 text-sm mt-0.5">{formatLongDate(new Date(), language)}</p>
        </div>
        {/* On a phone the icon alone: the German label left about 150 px of a
            390 px screen for the greeting, which then took three lines
            (ai-trainer-ops#131). From sm up the label alone, like every other
            button. */}
        <div className="flex shrink-0 gap-2">
          {/* A race is what the plan is built toward, and setting one should be
              as easy to find as an activity (ai-trainer-ops#175). */}
          <button
            type="button"
            onClick={() => setAddRaceOpen(true)}
            aria-label={t('Add race')}
            className={`${button} min-h-10 min-w-10 justify-center`}
          >
            <Flag size={15} aria-hidden="true" className="sm:hidden" />
            <span className="hidden sm:inline">{t('Add race')}</span>
          </button>
          <button
            type="button"
            onClick={() => setAddActivityOpen(true)}
            aria-label={t('Add activity')}
            className={`${button} min-h-10 min-w-10 justify-center`}
          >
            <Plus size={15} aria-hidden="true" className="sm:hidden" />
            <span className="hidden sm:inline">{t('Add activity')}</span>
          </button>
        </div>
      </div>
      {addRaceOpen && <AddRaceDialog onClose={() => setAddRaceOpen(false)} />}

      {/* A failed sync was silent: the rides just never came (ai-trainer-ops#95). */}
      {analysisStatus === 'error' && analysisError && (
        <p role="alert" className={note(false)}>
          {/* syncErrorMessage builds one of four fixed sentences (ai-trainer-ops#121). */}
          {t(analysisError)}
        </p>
      )}

      {/* What the athlete came for, before anything else on the page.  Also
          where the week strip below reports: it selects, this re-renders (#634). */}
      <SessionHero
        sessions={selectedSessions}
        date={selectedDate}
        doneKeys={selectedDoneKeys}
        onLog={setLoggingSession}
      />
      {isExpertMode && selectedDate === formatLocalDate(new Date()) && <DayBoardWhy />}
      {/* The app rebuilt the plan by itself, and says why (#158, #206). */}
      <PlanRebuildNotice />
      {loggingSession && (
        /* The same form as "Add activity", with this session as the answer to
           "which planned session was it?" (ai-trainer-ops#171). A failed save
           keeps the form, with the error and the save again (#127). */
        <LogSessionDialog
          key={`${loggingSession.date}#${loggingSession.slot ?? 0}`}
          open
          session={loggingSession}
          onLogPlanned={logSession}
          onClose={() => setLoggingSession(null)}
        />
      )}

      {/* Anything the plan and the sync do not know about: a ride on a rest
          day, a run instead of the ride, a day the plan never covered (#47).
          Opened from the header button, and mounted only while open so every
          opening starts from an empty form. It asks whether this was a planned
          session of the day, and logs it as that one if so (#170, #171). */}
      {addActivityOpen && (
        <LogSessionDialog open onLogPlanned={logSession} onClose={() => setAddActivityOpen(false)} />
      )}
      {editingActivity && (
        <LogSessionDialog
          /* Closing clears `editingActivity`, so this block unmounts and the
             next entry's form is a fresh mount — that, not this key, is what
             keeps one entry's values out of another's form, and the cancel test
             pins it. The key is kept only so the guarantee survives a refactor
             that renders the dialog unconditionally. */
          key={`${editingActivity.date}#${editingActivity.slot}`}
          open
          editing={editingActivity}
          onClose={() => setEditingActivity(null)}
        />
      )}

      {/* The week is the daily read; the month is a question asked occasionally,
          so it opens over the page instead of sitting under it (#621). */}
      {trainingPlan.length > 0 && (
        <WeekStrip
          plan={trainingPlan}
          selectedDate={selectedDate}
          onSelect={setSelectedDate}
          onShowMore={() => setCalendarOpen(true)}
        />
      )}

      <Modal open={calendarOpen} onClose={() => setCalendarOpen(false)} title={t('Training calendar')}>
        <TrainingCalendar showLoggedActivities />
      </Modal>

      {/* A ride the coach needs an answer about is the one piece of history
          that stays on the first screen (ai-trainer-ops#68), and above the
          chat: below a 60vh chat box it sat two screens down on a phone
          (review of 2026-11-01, ai-trainer-ops#338 F5). */}
      {ridesAwaitingAnswer.length > 0 && (
        <div className={card}>
          <h2 className={cardTitle}>{t('Needs your answer')}</h2>
          <div className="divide-y divide-gray-100">{ridesAwaitingAnswer.map(renderRide)}</div>
        </div>
      )}

      {/* The coach right after today and the week: on a phone it sat below the
          cold-start, season and countdown cards, almost two screens down, the
          last thing a new tester reached (review of 2026-11-01,
          ai-trainer-ops#338; #68 asked for today and the coach first). */}
      {/* Coach Timeline — the recent-training summary opens the conversation as a
          pinned coach entry, then plan updates, recommendations and chat interleave
          in the feed below (#418). Takes up the majority of the remaining space. */}
      <div className="flex flex-col">
        {/* `sr-only`, not deleted (ai-trainer-ops#51.6). The words appeared
            twice, a hand's width apart: here as the section label, and again as
            the card's own title inside `AIChat`.

            Hidden rather than removed because the card's title is a `<span>`,
            so this `<h2>` is the only landmark a screen reader can navigate to
            — and the section holds more than the card, the training-status
            summary sits above it. Deleting the heading would take away the one
            structural handle on that whole region to fix something only sighted
            readers can see.

            `AIChat` keeps its title: it is also used on the day view, where it
            is the only thing naming the card. */}
        <h2 className="sr-only">{t('Coach Timeline')}</h2>

        <TrainingSummaryCard summaryLoading={summaryLoading} />

        <AIChat contextWorkout={coachContextWorkout} className="md:h-[60vh] md:min-h-[24rem]" />
      </div>

      {/* What a plan without rides rests on (ai-trainer-ops#89). */}
      <ColdStartNote />
      {/* The race the athlete trains for stays in view: it renders only when
          one is ahead, and it is the goal, not history (ai-trainer-ops#68). */}
      <SeasonCountdown />

      {/* What the weeks are for: this one and the ones after it
          (ai-trainer-ops#98). */}
      {trainingPlan.length > 0 && <SeasonPlan />}

      {/* Strava history analysis progress */}
      <ActivityAnalysisProgress />

      {/* Everything else is one tap away, not stacked under the coach
          (ai-trainer-ops#68; the owner: "the chat is the interface"). A native
          disclosure: no state, keyboard and screen reader work as they are. */}
      {(otherRides.length > 0 || hasScoredPredictions || isExpertMode) && (
      <details className={`group ${card}`}>
        <summary className={`cursor-pointer select-none ${cardTitle}`}>
          {t('More: recent activities and predictions')}
        </summary>
        <div className="mt-3 space-y-5">
          {otherRides.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-900">{t('Recent activities')}</h2>
              <div className="divide-y divide-gray-100">{otherRides.map(renderRide)}</div>
            </div>
          )}

          {/* How often the coach's checkable predictions came true, misses
              included (ai-trainer-ops#16). Renders nothing until one is scored. */}
          <PredictionScoreboard />

          {/* Everything below here is expert mode, gathered in one place so turning
              it on adds a block at the bottom rather than pushing the coach down. */}
          {isExpertMode && (
            <p className="text-sm text-gray-500">
              {t('Consumed tokens')}:{' '}
              <span className="font-medium text-gray-900">{consumedTokens.toLocaleString(localeTag(language))}</span>
            </p>
          )}

          {/* Form and fitness over time: a diagnostic, not a daily read.  #619
              promoted it onto the front page; it belongs behind expert mode. */}
          {isExpertMode && (
            // Nothing while its code arrives; the rest of the dashboard stays.
            <Suspense fallback={null}>
              <ProgressionChart />
            </Suspense>
          )}

          {/* Coach's model of the athlete + its confidences — expert mode only */}
          {isExpertMode && <AthletePerformanceModelCard />}

          {/* Recent plan changes / override analytics — expert mode only (#357) */}
          {isExpertMode && authToken && <PlanChangesPanel authToken={authToken} />}
        </div>
      </details>
      )}
    </div>
  )
}
