import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type { WheelEvent } from 'react'
import {
  Send,
  Bot,
  User,
  Brain,
  Trash2,
  CalendarCheck,
  BookOpen,
  RotateCcw,
  Scale,
  HelpCircle,
  Lightbulb,
  FlaskConical,
  Pin,
  Settings,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import ReactMarkdown from 'react-markdown'
import { MARKDOWN_COMPONENTS } from './coachMarkdown'
import ReplyConfirmations from './ReplyConfirmations'
import ReportedSessionOffer from './ReportedSessionOffer'
import RaceOffer from './RaceOffer'
import { alreadyRecorded, parseReportedSession } from '../utils/reportedSession'
import CoachSaw from './CoachSaw'
import WrongFlag from './WrongFlag'
import { useAppStore } from '../store/useAppStore'
import { askTrainer, refreshLoginSummary } from '../services/ai'
import { REASONING_SOURCE_META, REASONING_BADGE_CLASS } from '../utils/reasoningSource'
import {
  answerAthleteInquiry,
  clearChatHistoryRemote,
  dismissAthleteInquiry,
  fetchCoachMemory,
  fetchCurrentUser,
  fetchPlanHistory,
  fetchAthleteInquiries,
  fetchAthleteOpenQuestions,
  fetchAthleteHypotheses,
  fetchValidationExperiments,
} from '../services/user'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteInquiry,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'
import { planUpdateEvents, recommendationEvents } from '../utils/coachTimeline'
import type { TimelineEvent, TimelineEventKind } from '../utils/coachTimeline'
import type { TrainingDay, ChatMessage } from '../store/useAppStore'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'

const VISIBLE_ITEM_LIMIT = 4

const EVENT_META: Record<TimelineEventKind, { icon: LucideIcon; iconClass: string }> = {
  'plan-update': { icon: CalendarCheck, iconClass: 'text-green-600' },
  'open-question': { icon: HelpCircle, iconClass: 'text-blue-500' },
  hypothesis: { icon: Lightbulb, iconClass: 'text-amber-500' },
  experiment: { icon: FlaskConical, iconClass: 'text-purple-500' },
}

interface Props {
  contextWorkout?: TrainingDay
  className?: string
}

interface ChatExchange {
  id: string
  messages: ChatMessage[]
}

function groupMessagesIntoExchanges(messages: ChatMessage[]): ChatExchange[] {
  const exchanges: ChatExchange[] = []
  let current: ChatMessage[] = []

  messages.forEach((msg, index) => {
    if (msg.role === 'user') {
      if (current.length > 0) {
        exchanges.push({ id: `exchange-${exchanges.length}`, messages: current })
      }
      current = [msg]
      return
    }

    if (current.length === 0) {
      exchanges.push({ id: `exchange-${exchanges.length}`, messages: [msg] })
      return
    }

    current.push(msg)

    if (index === messages.length - 1) {
      exchanges.push({ id: `exchange-${exchanges.length}`, messages: current })
      current = []
    }
  })

  if (current.length > 0) {
    exchanges.push({ id: `exchange-${exchanges.length}`, messages: current })
  }

  return exchanges.reverse()
}

// The unified Coach Timeline feed: conversation exchanges and system events share
// one chronological (newest-first) stream so plan updates and recommendations read
// in context rather than as separate cards (#418).
type FeedItem =
  | { type: 'exchange'; id: string; timestamp: string; exchange: ChatExchange }
  | { type: 'event'; id: string; timestamp: string; event: TimelineEvent }

function buildFeed(exchanges: ChatExchange[], events: TimelineEvent[]): FeedItem[] {
  const exchangeItems: FeedItem[] = exchanges.map((exchange) => ({
    type: 'exchange',
    id: exchange.id,
    // An exchange is placed by its most recent message; the empty welcome state
    // sorts to the end (no timestamp) so real events surface above it.
    timestamp: exchange.messages.at(-1)?.timestamp ?? '',
    exchange,
  }))
  const eventItems: FeedItem[] = events.map((event) => ({
    type: 'event',
    id: event.id,
    timestamp: event.timestamp,
    event,
  }))
  return [...exchangeItems, ...eventItems].sort((a, b) =>
    a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0,
  )
}

// A question the coach could never answer from data, pinned above the feed until
// the athlete deals with it (#506). It sits outside the scrolling feed on purpose:
// as an ordinary chat message it would be buried by the next exchange, which is
// exactly how these questions went unanswered before.
function PinnedInquiry({
  inquiry,
  onAnswer,
  onSkip,
}: {
  inquiry: AthleteInquiry
  onAnswer: (answer: string, option?: number) => Promise<void>
  onSkip: () => Promise<void>
}) {
  const t = useT()
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // A fresh question after a rephrasing must not inherit the text that missed.
  useEffect(() => {
    setAnswer('')
    setError('')
  }, [inquiry.id, inquiry.question])

  const submit = async () => {
    if (!answer.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      await onAnswer(answer.trim())
      setAnswer('')
    } catch {
      setError("Couldn't send that answer — try again.")
    } finally {
      setBusy(false)
    }
  }

  // One tap answers (ai-trainer-ops#155). The index is what the server reads;
  // the label is what the chat shows.
  const tap = async (label: string, option: number) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await onAnswer(label, option)
    } catch {
      setError("Couldn't send that answer — try again.")
    } finally {
      setBusy(false)
    }
  }

  // No in-flight guard here: both buttons carry `disabled={busy}`, so a second
  // click cannot reach this. `submit` still needs its own check because Enter can
  // fire on an empty box.
  const skip = async () => {
    setBusy(true)
    try {
      await onSkip()
    } catch {
      setError("Couldn't skip that — try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      data-testid="pinned-inquiry"
      className="mx-3 mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5"
    >
      <p className="mb-1.5 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-amber-600">
        <Pin size={11} />
        {inquiry.askCount > 1 ? t('Still needs an answer') : t('Your coach needs an answer')}
      </p>
      <p className="text-sm text-gray-800">{inquiry.question}</p>
      {inquiry.whyAsking && (
        <p className="mt-1 text-[11px] text-gray-500">{inquiry.whyAsking}</p>
      )}
      {inquiry.options && inquiry.options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label={t('Answers to tap')}>
          {inquiry.options.map((label, index) => (
            <button
              key={`${index}-${label}`}
              type="button"
              onClick={() => void tap(label, index)}
              disabled={busy}
              className="rounded-full border border-amber-300 bg-white px-3 py-1 text-sm text-gray-800 transition-colors hover:bg-amber-100 disabled:opacity-50"
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            void submit()
          }
        }}
        placeholder={t('Type your answer...')}
        aria-label={t('Answer: {question}', { question: inquiry.question })}
        rows={2}
        disabled={busy}
        className="mt-2 w-full resize-none rounded-lg border border-amber-200 bg-white px-2.5 py-1.5 text-sm focus:border-amber-500 focus:ring-amber-500 disabled:opacity-50"
      />
      <div className="mt-1.5 flex items-center gap-2">
        <button
          onClick={() => void submit()}
          disabled={busy || !answer.trim()}
          className="rounded-lg bg-amber-500 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-amber-600 disabled:opacity-50"
        >
          {t('Send')}
        </button>
        <button
          onClick={() => void skip()}
          disabled={busy}
          className="rounded-lg px-2 py-1 text-xs font-medium text-gray-500 transition-colors hover:text-gray-700 disabled:opacity-50"
        >
          {t('Skip for now')}
        </button>
        {inquiry.settingsHint && (
          <span className="ml-auto flex items-center gap-1 text-[11px] text-gray-400">
            <Settings size={11} />
            {inquiry.settingsHint}
          </span>
        )}
      </div>
      {error && <p className="mt-1 text-[11px] text-red-600">{error}</p>}
    </div>
  )
}

export default function AIChat({ contextWorkout, className }: Props) {
  const t = useT()
  const planText = usePlanText()
  const {
    authToken,
    userProfile,
    chatHistory,
    coachMemory,
    addChatMessage,
    setUserProfile,
    setCoachMemory,
    clearChatHistory,
    setTrainingPlan,
    updateTrainingDay,
    updateRideMetricLabel,
    pendingCoachMessage,
    setPendingCoachMessage,
    setRiderAssessment,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      chatHistory: s.chatHistory,
      coachMemory: s.coachMemory,
      addChatMessage: s.addChatMessage,
      setUserProfile: s.setUserProfile,
      setCoachMemory: s.setCoachMemory,
      clearChatHistory: s.clearChatHistory,
      setTrainingPlan: s.setTrainingPlan,
      updateTrainingDay: s.updateTrainingDay,
      updateRideMetricLabel: s.updateRideMetricLabel,
      pendingCoachMessage: s.pendingCoachMessage,
      setPendingCoachMessage: s.setPendingCoachMessage,
      setRiderAssessment: s.setRiderAssessment,
    }))
  )

  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showMemory, setShowMemory] = useState(false)
  const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(null)
  const [staleRefreshWarning, setStaleRefreshWarning] = useState(false)
  // The raw lists, not their sentences: the sentences follow the interface
  // language, which can change while the chat is open (ai-trainer-ops#103 Q27).
  const [timelineSource, setTimelineSource] = useState<{
    history: PlanDayHistoryEntry[]
    questions: AthleteOpenQuestion[]
    hypotheses: AthleteHypothesis[]
    experiments: AthleteExperiment[]
  }>({ history: [], questions: [], hypotheses: [], experiments: [] })
  const language = useLanguage()
  const timelineEvents: TimelineEvent[] = useMemo(
    () => [
      ...planUpdateEvents(timelineSource.history, t, localeTag(language)),
      ...recommendationEvents(
        timelineSource.questions,
        timelineSource.hypotheses,
        timelineSource.experiments,
        t,
      ),
    ],
    [timelineSource, t, language],
  )
  const [pendingInquiries, setPendingInquiries] = useState<AthleteInquiry[]>([])
  const [visibleItemCount, setVisibleItemCount] = useState(VISIBLE_ITEM_LIMIT)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const sendInFlightRef = useRef(false)
  // A question-card answer to send on to the coach once the chat request in
  // flight has finished (ai-trainer-ops#103 Q28): sendMessage refuses a second
  // send while one runs, and the forward must not be lost to that.
  const pendingForwardRef = useRef<string | null>(null)
  // Inquiries the athlete has already answered or skipped. A list fetch can be in
  // flight when they deal with one — and a skip changes no chat message, so the
  // effect below is not even re-run to cancel it. Re-pinning a question they just
  // answered is worse than picking up a new one a cycle late.
  const handledInquiryIdsRef = useRef<Set<string>>(new Set())
  // Rephrased questions the athlete has not answered yet, keyed by id. Answering
  // appends chat messages, which re-runs the list effect below — so a fetch
  // carrying the pre-rephrase wording can resolve after the rephrase is applied
  // and roll the pin back to the question the coach already moved on from.
  // handledInquiryIdsRef cannot cover this: a rephrase is still pending, so the
  // question must stay pinned rather than be filtered out.
  const rephrasedInquiriesRef = useRef<Map<string, AthleteInquiry>>(new Map())
  // Stable ref so the pendingCoachMessage effect always calls the latest sendMessage
  const sendMessageRef = useRef<((msg: string) => Promise<void>) | null>(null)

  // Auto-grow the textarea as the user types
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 128)}px`
    }
  }, [input])

  const welcomeContent = contextWorkout
    ? t("Hey, good to see you. Let's look at today's {title} session together. How are you feeling about it?", { title: planText(contextWorkout.title) })
    : t('Hey, good to see you. How are you feeling about training today? We can talk plan, recovery, nutrition, or technique.')

  const displayMessages: ChatMessage[] =
    chatHistory.length > 0
      ? chatHistory
      : [{ role: 'assistant', content: welcomeContent, timestamp: '' }]
  const displayExchanges = groupMessagesIntoExchanges(displayMessages)
  const feedItems = buildFeed(displayExchanges, timelineEvents)
  const visibleFeedItems = feedItems.slice(0, visibleItemCount)
  const olderItemCount = Math.max(feedItems.length - visibleItemCount, 0)
  // The active exchange (a just-sent question awaiting its answer) always sorts to
  // the top of the feed, so the typing indicator belongs to the first item.
  const firstItem = visibleFeedItems[0]
  const showLoadingInLatestExchange =
    loading &&
    firstItem?.type === 'exchange' &&
    firstItem.exchange.messages.at(-1)?.role === 'user'

  // Pull plan changes and open learning-pipeline recommendations into the timeline.
  // Re-fetched after each exchange since a coach reply may adjust the plan or raise
  // new questions. Failures degrade to no events rather than breaking the chat.
  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    void (async () => {
      const [history, questions, hypotheses, experiments, inquiries] = await Promise.all([
        fetchPlanHistory(authToken).catch(() => [] as PlanDayHistoryEntry[]),
        fetchAthleteOpenQuestions(authToken).catch(() => [] as AthleteOpenQuestion[]),
        fetchAthleteHypotheses(authToken).catch(() => [] as AthleteHypothesis[]),
        fetchValidationExperiments(authToken).catch(() => [] as AthleteExperiment[]),
        fetchAthleteInquiries(authToken).catch(() => [] as AthleteInquiry[]),
      ])
      if (cancelled) return
      setTimelineSource({ history, questions, hypotheses, experiments })
      setPendingInquiries(
        inquiries
          .filter((item) => !handledInquiryIdsRef.current.has(item.id))
          .map((item) => {
            // askCount only ever grows, so it orders the two versions without
            // depending on which request resolved first.
            const local = rephrasedInquiriesRef.current.get(item.id)
            return local && local.askCount > item.askCount ? local : item
          }),
      )
    })()
    return () => {
      cancelled = true
    }
  }, [authToken, chatHistory.length])

  useEffect(() => {
    setVisibleItemCount(VISIBLE_ITEM_LIMIT)
  }, [chatHistory.length])

  const loadOlderItems = useCallback(() => {
    setVisibleItemCount((count) => Math.min(count + VISIBLE_ITEM_LIMIT, feedItems.length))
  }, [feedItems.length])

  const handleMessagesScroll = () => {
    const container = messagesRef.current
    if (!container || olderItemCount === 0) return

    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    if (distanceFromBottom < 96) {
      loadOlderItems()
    }
  }

  const handleMessagesWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY > 0 && olderItemCount > 0) {
      loadOlderItems()
    }
  }

  const renderMessage = (msg: ChatMessage, key: string) => (
    <div key={key} className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      {msg.role === 'assistant' && (
        <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center shrink-0">
          <Bot size={14} className="text-amber-600" />
        </div>
      )}
      <div
        className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
          msg.role === 'user'
            ? 'bg-amber-500 text-white rounded-br-sm'
            : 'bg-gray-100 text-gray-800 rounded-bl-sm'
        }`}
      >
        {msg.planUpdateCount ? (
          <>
            {msg.role === 'assistant' ? (
              <ReactMarkdown components={MARKDOWN_COMPONENTS}>{msg.content}</ReactMarkdown>
            ) : (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            )}
            <p className="mt-2 flex items-center gap-1 text-xs font-medium text-green-700 bg-green-100 rounded-lg px-2 py-1">
              <CalendarCheck size={12} />
              {msg.planUpdateCount === 1
                ? t('Training plan updated: 1 day modified.')
                : t('Training plan updated: {count} days modified.', { count: msg.planUpdateCount })}
            </p>
          </>
        ) : msg.role === 'assistant' ? (
          <ReactMarkdown components={MARKDOWN_COMPONENTS}>{msg.content}</ReactMarkdown>
        ) : (
          <span className="whitespace-pre-wrap">{msg.content}</span>
        )}
        {msg.role === 'assistant' && <ReplyConfirmations msg={msg} />}
        {msg.role === 'assistant' && msg.raceOffer && (
          <RaceOffer date={msg.raceOffer.date} replaces={msg.raceOffer.replaces} />
        )}
        {msg.role === 'assistant' && msg.reportedSession && (
          <ReportedSessionOffer session={msg.reportedSession} />
        )}
        {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
          <div className="mt-2 border-t border-gray-200 pt-2">
            <p className="flex items-center gap-1 text-xs font-semibold text-gray-500 mb-1">
              <BookOpen size={11} />
              {t('Sources')}
            </p>
            <ul className="space-y-0.5">
              {msg.sources.map((source, idx) => (
                <li key={idx} className="text-xs text-gray-500">
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:text-amber-600 underline"
                    >
                      {source.title}
                    </a>
                  ) : (
                    source.title
                  )}
                  {source.doi && (
                    <span className="ml-1 text-gray-400">· DOI: {source.doi}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {msg.role === 'assistant' &&
          (msg.physiologyRationale || msg.contextRationale || msg.objectiveRationale) && (
          <details className="mt-2 border-t border-gray-200 pt-2">
            <summary className="flex cursor-pointer items-center gap-1 text-xs font-semibold text-gray-500 hover:text-amber-600">
              <Scale size={11} />
              {t('Why this answer?')}
            </summary>
            <dl className="mt-1 space-y-1">
              {/* Rationale layers map onto the shared knowledge sources (#377):
                  the physiology read is a coach inference, the personal-context
                  read is a personal observation. Cited science, when any, shows
                  in the Sources block above as the scientific-evidence source.

                  The objective link (#565) is deliberately NOT a fourth source
                  badge: the three above say where a claim came from, this says
                  what it is for. It leads the list because it is the part the
                  athlete came for — the rest explains how it was arrived at. */}
              {msg.objectiveRationale && (
                <div className="text-xs text-gray-600">
                  <dt className="inline">
                    <span className={`mr-1.5 ${REASONING_BADGE_CLASS} bg-amber-50 text-amber-700 border border-amber-100`}>
                      {t('What this buys you')}
                    </span>
                  </dt>
                  <dd className="inline">{msg.objectiveRationale}</dd>
                </div>
              )}
              {msg.physiologyRationale && (
                <div className="text-xs text-gray-500">
                  <dt className="inline">
                    <span
                      className={`mr-1.5 ${REASONING_BADGE_CLASS} ${REASONING_SOURCE_META.coach_inference.className}`}
                    >
                      {t(REASONING_SOURCE_META.coach_inference.label)}
                    </span>
                  </dt>
                  <dd className="inline">{msg.physiologyRationale}</dd>
                </div>
              )}
              {msg.contextRationale && (
                <div className="text-xs text-gray-500">
                  <dt className="inline">
                    <span
                      className={`mr-1.5 ${REASONING_BADGE_CLASS} ${REASONING_SOURCE_META.personal_observation.className}`}
                    >
                      {t(REASONING_SOURCE_META.personal_observation.label)}
                    </span>
                  </dt>
                  <dd className="inline">{msg.contextRationale}</dd>
                </div>
              )}
            </dl>
          </details>
        )}
        {msg.role === 'assistant' && msg.coachSaw && <CoachSaw items={msg.coachSaw} />}
        {/* Not on a failure notice: that is the app talking, not the coach. Not on
            the welcome either: it is the app's text, has no reference, and the
            server refused the flag (ai-trainer-ops#131). */}
        {msg.role === 'assistant' && !msg.failedUserMessage && msg.timestamp && (
          <WrongFlag target="reply" flagRef={msg.timestamp} excerpt={msg.content} />
        )}
        {msg.role === 'assistant' && msg.failedUserMessage && lastFailedMessage === msg.failedUserMessage && (
          <button
            onClick={() => void sendMessage(msg.failedUserMessage, { skipAddUserMessage: true })}
            disabled={loading || !authToken || !userProfile}
            aria-label={t('Retry coach response')}
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
          >
            <RotateCcw size={12} />
            {t('Retry')}
          </button>
        )}
      </div>
      {msg.role === 'user' && (
        <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
          <User size={14} className="text-gray-600" />
        </div>
      )}
    </div>
  )

  const renderLoadingIndicator = () => (
    <div className="flex gap-2 justify-start">
      <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center">
        <Bot size={14} className="text-amber-600" />
      </div>
      <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-2">
        <div className="flex gap-1">
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
      </div>
    </div>
  )

  const renderTimelineEvent = (event: TimelineEvent) => {
    const { icon: Icon, iconClass } = EVENT_META[event.kind]
    // A plan change is a log line, not a message: "PLAN GENERATION · 14 days
    // updated" used to weigh as much as the coach and was the first thing a
    // new account read (ai-trainer-ops#91). One muted line; the days on request.
    if (event.kind === 'plan-update') {
      const line = (
        <span className="flex items-center gap-1.5">
          <Icon size={12} className="shrink-0" aria-hidden="true" />
          <span className="font-medium">{event.title}</span>
          <span aria-hidden="true">·</span>
          <span>{event.body}</span>
        </span>
      )
      return event.details?.length ? (
        <details
          key={event.id}
          data-testid="timeline-event"
          data-event-kind={event.kind}
          className="px-1 text-xs text-gray-400"
        >
          <summary className="cursor-pointer list-none hover:text-gray-600">{line}</summary>
          <ul className="mt-1 ml-5 space-y-0.5 text-gray-500">
            {event.details.map((detail, i) => (
              <li key={i}>{detail}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p key={event.id} data-testid="timeline-event" data-event-kind={event.kind} className="px-1 text-xs text-gray-400">
          {line}
        </p>
      )
    }
    return (
      <div
        key={event.id}
        data-testid="timeline-event"
        data-event-kind={event.kind}
        className="flex items-start gap-2.5 rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2"
      >
        <Icon size={16} className={`mt-0.5 shrink-0 ${iconClass}`} />
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">{event.title}</p>
          <p className="text-sm text-gray-700">{event.body}</p>
        </div>
      </div>
    )
  }

  /** A session the athlete just reported, unless a device already brought it
   *  in (ai-trainer-ops#166). */
  function offerToEnter(text: string) {
    const reported = parseReportedSession(text)
    if (!reported || alreadyRecorded(reported, useAppStore.getState().rideMetricsHistory)) {
      return undefined
    }
    return reported
  }

  async function sendMessage(
    msgOverride?: string,
    options?: { skipAddUserMessage?: boolean; alreadyInChat?: boolean },
  ) {
    const raw = typeof msgOverride === 'string' ? msgOverride : input
    // The ref, not `loading`: a closure from an earlier render can still see
    // `loading` true after the send it belonged to has finished.
    if (!raw.trim() || sendInFlightRef.current) return

    const userMsg = raw.trim()
    const timestamp = new Date().toISOString()
    // Only clear the textarea when sending what was typed in it
    if (typeof msgOverride !== 'string') setInput('')

    if (!userProfile || !authToken) return

    if (!options?.skipAddUserMessage) {
      addChatMessage({ role: 'user', content: userMsg, timestamp })
    }
    sendInFlightRef.current = true
    setLoading(true)

    try {
      const result = await askTrainer(userMsg, authToken, {
        contextWorkout,
        alreadyInChat: options?.alreadyInChat,
      })

      let planUpdateCount = 0
      const planUpdates = result.planUpdates ?? []
      if (result.updatedPlan) {
        setTrainingPlan(result.updatedPlan)
        planUpdateCount = planUpdates.length
      } else if (planUpdates.length > 0) {
        for (const update of planUpdates) {
          const { date, ...fields } = update
          updateTrainingDay(date, fields)
        }
        planUpdateCount = planUpdates.length
      }
      // The backend drops the training summary whenever the plan changes, but the
      // dashboard asks for a new one only when it loads. Without this the card
      // kept the old plan ("Friday: rest day") beside the changed week. Before
      // the first analysed ride there is no assessment and so no summary; the
      // request was a 404 that still spent the AI allowance (ai-trainer-ops#183).
      if (planUpdateCount > 0 && useAppStore.getState().riderAssessment) {
        refreshLoginSummary(authToken)
          .then((loginSummary) => {
            const current = useAppStore.getState().riderAssessment
            if (loginSummary && current) setRiderAssessment({ ...current, loginSummary })
          })
          .catch(() => {
            // The old summary stays until the next dashboard load regenerates it.
          })
      }

      if (result.rideLabelUpdates && result.rideLabelUpdates.length > 0) {
        for (const labelUpdate of result.rideLabelUpdates) {
          updateRideMetricLabel(labelUpdate.stravaActivityId, labelUpdate.labelOverride)
        }
      }

      addChatMessage({
        role: 'assistant',
        content: result.response,
        timestamp: new Date().toISOString(),
        planUpdateCount: planUpdateCount > 0 ? planUpdateCount : undefined,
        sources: result.sources?.length ? result.sources : undefined,
        physiologyRationale: result.physiologyRationale || undefined,
        contextRationale: result.contextRationale || undefined,
        objectiveRationale: result.objectiveRationale || undefined,
        capturedGoals: result.capturedGoals?.length ? result.capturedGoals : undefined,
        capturedStates: result.capturedStates?.length ? result.capturedStates : undefined,
        capturedConstraints: result.capturedConstraints?.length
          ? result.capturedConstraints
          : undefined,
        capturedDayPreferences: result.capturedDayPreferences?.length
          ? result.capturedDayPreferences
          : undefined,
        coachSaw: result.coachSaw?.length ? result.coachSaw : undefined,
        raceProposal: result.raceProposal ?? undefined,
        raceOffer: result.raceOffer ?? undefined,
        profileProposal: result.profileProposal ?? undefined,
        reportedSession: offerToEnter(userMsg),
      })
      setLastFailedMessage(null)

      // Re-sync coach memory and profile from server (backend updated both inside ask_trainer).
      // Retry once on transient failure; show a warning if the refresh still can't complete.
      const retry = <T,>(fn: () => Promise<T>) => fn().catch(() => fn())
      const refreshResults = await Promise.allSettled([
        retry(() => fetchCoachMemory(authToken).then((m) => setCoachMemory(m))),
        retry(() => fetchCurrentUser(authToken).then((u) => setUserProfile(u.profile))),
      ])
      if (refreshResults.some((r) => r.status === 'rejected')) {
        setStaleRefreshWarning(true)
      } else {
        setStaleRefreshWarning(false)
      }
    } catch (err) {
      setLastFailedMessage(userMsg)
      // An exhausted free allowance is not a failure to apologise for: the
      // server says what to do about it (ai-trainer-ops#79). Any other failure
      // says what happened too: the backend and the client word every one of
      // them now (ai-trainer-ops#95).
      const allowanceSpent = (err as { status?: number } | null)?.status === 402
      const reason = err instanceof Error ? err.message : ''
      // In the interface's language, and the place named as the page names it
      // (ai-trainer-ops#121). The server's reason is translated when de.ts
      // knows it; one with numbers in it stays as the server wrote it.
      const translatedReason = reason ? t(reason) : ''
      addChatMessage({
        role: 'assistant',
        content: allowanceSpent
          ? `${translatedReason}\n\n${t('Coach & AI')} → ${t('Your AI Provider Key')}.`
          : `${t('Sorry, I could not answer.')} ${translatedReason}`.trim(),
        timestamp: new Date().toISOString(),
        failedUserMessage: userMsg,
      })
    } finally {
      sendInFlightRef.current = false
      setLoading(false)
      const forward = pendingForwardRef.current
      if (forward) {
        pendingForwardRef.current = null
        void sendMessage(forward, { skipAddUserMessage: true, alreadyInChat: true })
      }
    }
  }

  // Keep the ref current so the effect below always invokes the latest closure
  sendMessageRef.current = (msg: string) => sendMessage(msg)

  // Auto-send a message triggered externally (e.g. match-score badge click)
  useEffect(() => {
    if (!pendingCoachMessage) return
    setPendingCoachMessage(null)
    void sendMessageRef.current?.(pendingCoachMessage)
  }, [pendingCoachMessage, setPendingCoachMessage])

  // The oldest unanswered question gets the pin: one question at a time, and the
  // one that has been waiting longest rather than the newest distraction.
  const pinnedInquiry = pendingInquiries[0]

  const handleInquiryAnswer = async (inquiry: AthleteInquiry, answer: string, option?: number) => {
    if (!authToken) return
    const result =
      option === undefined
        ? await answerAthleteInquiry(authToken, inquiry.id, answer)
        : await answerAthleteInquiry(authToken, inquiry.id, answer, option)
    // Mirror what the backend just persisted into the chat, so the exchange stays
    // readable in the timeline after the pin clears.
    const timestamp = new Date().toISOString()
    addChatMessage({ role: 'assistant', content: inquiry.question, timestamp })
    addChatMessage({ role: 'user', content: answer, timestamp })
    if (result.coachReply) {
      addChatMessage({ role: 'assistant', content: result.coachReply, timestamp })
    }
    // A rephrased question comes back still pending and re-pins itself; anything
    // else (answered, or handed off to settings) leaves the pin for good.
    if (result.inquiry.status !== 'pending') {
      handledInquiryIdsRef.current.add(result.inquiry.id)
      rephrasedInquiriesRef.current.delete(result.inquiry.id)
    } else {
      rephrasedInquiriesRef.current.set(result.inquiry.id, result.inquiry)
    }
    setPendingInquiries((current) =>
      result.inquiry.status === 'pending'
        ? current.map((item) => (item.id === result.inquiry.id ? result.inquiry : item))
        : current.filter((item) => item.id !== result.inquiry.id),
    )
    // The card's judge cannot change the plan. An answer that also asks for
    // something, such as a ride tomorrow, goes on to the coach as well
    // (ai-trainer-ops#103 Q28), whether or not it answered the question. It is
    // already in the timeline, so it is neither shown nor stored again.
    if (result.forwardToCoach) {
      if (sendInFlightRef.current) {
        pendingForwardRef.current = answer
      } else {
        await sendMessage(answer, { skipAddUserMessage: true, alreadyInChat: true })
      }
    }
  }

  const handleInquirySkip = async (inquiry: AthleteInquiry) => {
    if (!authToken) return
    await dismissAthleteInquiry(authToken, inquiry.id)
    handledInquiryIdsRef.current.add(inquiry.id)
    rephrasedInquiriesRef.current.delete(inquiry.id)
    setPendingInquiries((current) => current.filter((item) => item.id !== inquiry.id))
  }

  const handleClearChatHistory = async () => {
    if (!authToken) return
    clearChatHistory()
    try {
      await clearChatHistoryRemote(authToken)
    } catch {
      // keep local state cleared
    }
  }

  return (
    <div className={`bg-white rounded-xl border border-gray-100 flex flex-col ${className ?? 'shadow-xs h-[28rem]'}`}>
      {/* Header */}
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-amber-500" />
          <span className="font-semibold text-sm text-gray-800">{t('Coach Timeline')}</span>
        </div>
        <div className="flex items-center gap-1">
          {coachMemory && (
            <button
              onClick={() => setShowMemory((v) => !v)}
              title={t('Coach memory')}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors ${
                showMemory
                  ? 'bg-amber-100 text-amber-700'
                  : 'text-gray-400 hover:text-amber-600 hover:bg-amber-50'
              }`}
            >
              <Brain size={13} />
              {t('Memory')}
            </button>
          )}
          {chatHistory.length > 0 && (
            <button
              onClick={() => void handleClearChatHistory()}
              title={t('Clear chat history')}
              aria-label={t('Clear chat history')}
              className="inline-flex min-h-10 min-w-10 items-center justify-center p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Stale-refresh warning */}
      {staleRefreshWarning && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
          {t("Couldn't refresh coach state — data may be slightly out of date.")}
        </div>
      )}

      {pinnedInquiry && (
        <PinnedInquiry
          inquiry={pinnedInquiry}
          onAnswer={(answer, option) => handleInquiryAnswer(pinnedInquiry, answer, option)}
          onSkip={() => handleInquirySkip(pinnedInquiry)}
        />
      )}

      {/* Input */}
      <div className="p-3 border-b flex gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void sendMessage()
            }
          }}
          placeholder={t('Ask your coach...')}
          aria-label={t('Message to coach')}
          rows={1}
          className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 resize-none"
        />
        <button
          onClick={() => void sendMessage()}
          disabled={loading || !input.trim()}
          aria-label={t('Send message')}
          className="bg-amber-500 text-white rounded-xl px-3 py-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
        >
          <Send size={16} />
        </button>
      </div>

      {/* Coach memory panel */}
      {showMemory && coachMemory && (
        <div className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 leading-relaxed">
          <p className="font-semibold mb-1 flex items-center gap-1">
            <Brain size={11} /> {t("Coach's notes about you")}
          </p>
          <p className="whitespace-pre-wrap">{coachMemory}</p>
        </div>
      )}

      {/* The feed is newest-first: conversation exchanges and system events (plan
          updates, recommendations) interleaved by time; each exchange reads question
          before answer. */}
      <div
        ref={messagesRef}
        aria-label={t('Coach chat messages')}
        onScroll={handleMessagesScroll}
        onWheel={handleMessagesWheel}
        className="flex-1 overflow-y-auto p-4 flex flex-col gap-4"
      >
        {visibleFeedItems.map((item, itemIndex) =>
          item.type === 'event' ? (
            renderTimelineEvent(item.event)
          ) : (
            <div
              key={item.id}
              className="flex flex-col gap-2 border-b border-gray-100 pb-4 last:border-b-0 last:pb-0"
            >
              {item.exchange.messages.map((msg, messageIndex) =>
                renderMessage(msg, `${item.id}-${messageIndex}`),
              )}
              {itemIndex === 0 && showLoadingInLatestExchange && renderLoadingIndicator()}
            </div>
          ),
        )}
        {loading && !showLoadingInLatestExchange && renderLoadingIndicator()}

        {olderItemCount > 0 && (
          <div
            className="relative -mt-2 flex min-h-[70%] justify-center pt-8"
            data-testid="older-history-fade"
            aria-hidden="true"
          >
            <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-linear-to-b from-white/20 via-white/85 to-white" />
            <div className="relative mt-8 h-1 w-16 rounded-full bg-gray-200" />
          </div>
        )}
      </div>

    </div>
  )
}
