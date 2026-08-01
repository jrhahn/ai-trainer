import { useState, useRef, useEffect, useCallback } from 'react'
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
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import { useAppStore } from '../store/useAppStore'
import { askTrainer } from '../services/ai'
import { REASONING_SOURCE_META, REASONING_BADGE_CLASS } from '../utils/reasoningSource'
import {
  clearChatHistoryRemote,
  fetchCoachMemory,
  fetchCurrentUser,
  fetchPlanHistory,
  fetchAthleteOpenQuestions,
  fetchAthleteHypotheses,
  fetchValidationExperiments,
} from '../services/user'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'
import { planUpdateEvents, recommendationEvents } from '../utils/coachTimeline'
import type { TimelineEvent, TimelineEventKind } from '../utils/coachTimeline'
import type { TrainingDay, ChatMessage } from '../store/useAppStore'

const VISIBLE_ITEM_LIMIT = 4

const EVENT_META: Record<TimelineEventKind, { icon: LucideIcon; iconClass: string }> = {
  'plan-update': { icon: CalendarCheck, iconClass: 'text-green-600' },
  'open-question': { icon: HelpCircle, iconClass: 'text-blue-500' },
  hypothesis: { icon: Lightbulb, iconClass: 'text-amber-500' },
  experiment: { icon: FlaskConical, iconClass: 'text-purple-500' },
}

// Render headings as plain paragraphs so the chat uses a uniform font size
const MARKDOWN_COMPONENTS: Components = {
  h1: 'p',
  h2: 'p',
  h3: 'p',
  h4: 'p',
  h5: 'p',
  h6: 'p',
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

export default function AIChat({ contextWorkout, className }: Props) {
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
    }))
  )

  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showMemory, setShowMemory] = useState(false)
  const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(null)
  const [staleRefreshWarning, setStaleRefreshWarning] = useState(false)
  const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([])
  const [visibleItemCount, setVisibleItemCount] = useState(VISIBLE_ITEM_LIMIT)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const sendInFlightRef = useRef(false)
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
    ? `Hey, good to see you. Let's look at today's ${contextWorkout.title} session together. How are you feeling about it?`
    : "Hey, good to see you. How are you feeling about training today? We can talk plan, recovery, nutrition, or technique."

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
      const [history, questions, hypotheses, experiments] = await Promise.all([
        fetchPlanHistory(authToken).catch(() => [] as PlanDayHistoryEntry[]),
        fetchAthleteOpenQuestions(authToken).catch(() => [] as AthleteOpenQuestion[]),
        fetchAthleteHypotheses(authToken).catch(() => [] as AthleteHypothesis[]),
        fetchValidationExperiments(authToken).catch(() => [] as AthleteExperiment[]),
      ])
      if (cancelled) return
      setTimelineEvents([
        ...planUpdateEvents(history),
        ...recommendationEvents(questions, hypotheses, experiments),
      ])
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
        <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
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
              Training plan updated:{' '}
              {msg.planUpdateCount === 1 ? '1 day modified.' : `${msg.planUpdateCount} days modified.`}
            </p>
          </>
        ) : msg.role === 'assistant' ? (
          <ReactMarkdown components={MARKDOWN_COMPONENTS}>{msg.content}</ReactMarkdown>
        ) : (
          <span className="whitespace-pre-wrap">{msg.content}</span>
        )}
        {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
          <div className="mt-2 border-t border-gray-200 pt-2">
            <p className="flex items-center gap-1 text-xs font-semibold text-gray-500 mb-1">
              <BookOpen size={11} />
              Sources
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
        {msg.role === 'assistant' && (msg.physiologyRationale || msg.contextRationale) && (
          <details className="mt-2 border-t border-gray-200 pt-2">
            <summary className="flex cursor-pointer items-center gap-1 text-xs font-semibold text-gray-500 hover:text-amber-600">
              <Scale size={11} />
              Why this advice?
            </summary>
            <dl className="mt-1 space-y-1">
              {/* Rationale layers map onto the shared knowledge sources (#377):
                  the physiology read is a coach inference, the personal-context
                  read is a personal observation. Cited science, when any, shows
                  in the Sources block above as the scientific-evidence source. */}
              {msg.physiologyRationale && (
                <div className="text-xs text-gray-500">
                  <dt className="inline">
                    <span
                      className={`mr-1.5 ${REASONING_BADGE_CLASS} ${REASONING_SOURCE_META.coach_inference.className}`}
                    >
                      {REASONING_SOURCE_META.coach_inference.label}
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
                      {REASONING_SOURCE_META.personal_observation.label}
                    </span>
                  </dt>
                  <dd className="inline">{msg.contextRationale}</dd>
                </div>
              )}
            </dl>
          </details>
        )}
        {msg.role === 'assistant' && msg.failedUserMessage && lastFailedMessage === msg.failedUserMessage && (
          <button
            onClick={() => void sendMessage(msg.failedUserMessage, { skipAddUserMessage: true })}
            disabled={loading || !authToken || !userProfile}
            aria-label="Retry coach response"
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
          >
            <RotateCcw size={12} />
            Retry
          </button>
        )}
      </div>
      {msg.role === 'user' && (
        <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center flex-shrink-0">
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
    return (
      <div
        key={event.id}
        data-testid="timeline-event"
        data-event-kind={event.kind}
        className="flex items-start gap-2.5 rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2"
      >
        <Icon size={16} className={`mt-0.5 flex-shrink-0 ${iconClass}`} />
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">{event.title}</p>
          <p className="text-sm text-gray-700">{event.body}</p>
        </div>
      </div>
    )
  }

  async function sendMessage(msgOverride?: string, options?: { skipAddUserMessage?: boolean }) {
    const raw = typeof msgOverride === 'string' ? msgOverride : input
    if (!raw.trim() || loading || sendInFlightRef.current) return

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
      const result = await askTrainer(userMsg, authToken, { contextWorkout })

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
    } catch {
      setLastFailedMessage(userMsg)
      addChatMessage({
        role: 'assistant',
        content: 'Sorry, something went wrong and I could not respond.',
        timestamp: new Date().toISOString(),
        failedUserMessage: userMsg,
      })
    } finally {
      sendInFlightRef.current = false
      setLoading(false)
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
    <div className={`bg-white rounded-xl border border-gray-100 flex flex-col ${className ?? 'shadow-sm h-[28rem]'}`}>
      {/* Header */}
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-amber-500" />
          <span className="font-semibold text-sm text-gray-800">Coach Timeline</span>
        </div>
        <div className="flex items-center gap-1">
          {coachMemory && (
            <button
              onClick={() => setShowMemory((v) => !v)}
              title="Coach memory"
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors ${
                showMemory
                  ? 'bg-amber-100 text-amber-700'
                  : 'text-gray-400 hover:text-amber-600 hover:bg-amber-50'
              }`}
            >
              <Brain size={13} />
              Memory
            </button>
          )}
          {chatHistory.length > 0 && (
            <button
              onClick={() => void handleClearChatHistory()}
              title="Clear chat history"
              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Stale-refresh warning */}
      {staleRefreshWarning && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
          Couldn't refresh coach state — data may be slightly out of date.
        </div>
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
          placeholder="Ask your coach..."
          aria-label="Message to coach"
          rows={1}
          className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 resize-none"
        />
        <button
          onClick={() => void sendMessage()}
          disabled={loading || !input.trim()}
          aria-label="Send message"
          className="bg-amber-500 text-white rounded-xl px-3 py-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
        >
          <Send size={16} />
        </button>
      </div>

      {/* Coach memory panel */}
      {showMemory && coachMemory && (
        <div className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 leading-relaxed">
          <p className="font-semibold mb-1 flex items-center gap-1">
            <Brain size={11} /> Coach's notes about you
          </p>
          <p className="whitespace-pre-wrap">{coachMemory}</p>
        </div>
      )}

      {/* The feed is newest-first: conversation exchanges and system events (plan
          updates, recommendations) interleaved by time; each exchange reads question
          before answer. */}
      <div
        ref={messagesRef}
        aria-label="Coach chat messages"
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
            <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-white/20 via-white/85 to-white" />
            <div className="relative mt-8 h-1 w-16 rounded-full bg-gray-200" />
          </div>
        )}
      </div>

    </div>
  )
}
