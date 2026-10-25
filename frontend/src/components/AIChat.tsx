import { useState, useRef, useEffect, useCallback } from 'react'
import type { WheelEvent } from 'react'
import { Send, Trash2 } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import ChatMessageView from './chat/ChatMessageView'
import PinnedInquiry from './chat/PinnedInquiry'
import TimelineEventView from './chat/TimelineEventView'
import { useAppStore } from '../store/useAppStore'
import { clearChatHistoryRemote } from '../services/user'
import { buildFeed, groupMessagesIntoExchanges } from '../utils/coachTimeline'
import type { TimelineEvent } from '../utils/coachTimeline'
import { enterSends } from '../utils/enterSends'
import type { TrainingDay, ChatMessage } from '../store/useAppStore'
import { useT } from '../i18n'
import { usePlanText } from '../hooks/usePlanTranslations'
import { useCoachTimeline } from '../hooks/useCoachTimeline'
import { useSendMessage } from '../hooks/useSendMessage'
import { button, cardFrame, cardTitle, iconDeleteButton, input as inputClass, note, textButton } from './ui'

const VISIBLE_ITEM_LIMIT = 4

interface Props {
  contextWorkout?: TrainingDay
  className?: string
}

export default function AIChat({ contextWorkout, className }: Props) {
  const t = useT()
  const planText = usePlanText()
  const noRidesYet = useAppStore((s) => s.rideMetricsHistory.length === 0)
  const { authToken, userProfile, chatHistory, coachMemory, clearChatHistory, setChatHistory, showWarning } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      chatHistory: s.chatHistory,
      coachMemory: s.coachMemory,
      clearChatHistory: s.clearChatHistory,
      setChatHistory: s.setChatHistory,
      showWarning: s.showWarning,
    }))
  )
  const {
    input,
    setInput,
    loading,
    slowReply,
    stopWaiting,
    lastFailedMessage,
    staleRefreshWarning,
    sendMessage,
    forwardToCoach,
  } = useSendMessage(contextWorkout)
  const { timelineEvents, pinnedInquiry, handleInquiryAnswer, handleInquirySkip } = useCoachTimeline(forwardToCoach)

  const [showMemory, setShowMemory] = useState(false)
  const [visibleItemCount, setVisibleItemCount] = useState(VISIBLE_ITEM_LIMIT)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)

  // Auto-grow the textarea as the user types
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 128)}px`
    }
  }, [input])

  // An empty chat, the first visit's or a cleared one's, still says what one can
  // write, and before the first ride why the week is easy (ai-trainer-ops#224).
  // Not the opening message again: that one is about the day the plan was made.
  const welcomeContent = contextWorkout
    ? t("Hey, good to see you. Let's look at today's {title} session together. How are you feeling about it?", { title: planText(contextWorkout.title) })
    : [
        t('Hey, good to see you. How are you feeling about training today? We can talk plan, recovery, nutrition, or technique.'),
        noRidesYet &&
          t('I have not seen a ride of yours yet, so the first week starts easy: the first sessions measure where you are.'),
        [
          t('Just write to me here, for example:'),
          ...[t('I have no time on Saturday.'), t("I'm tired today, can we go easier?"), t('I have a race on 12 May.')].map(
            (example) => `- “${example}”`,
          ),
        ].join('\n'),
      ]
        .filter(Boolean)
        .join('\n\n')

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
    <ChatMessageView
      key={key}
      msg={msg}
      lastFailedMessage={lastFailedMessage}
      retryDisabled={loading || !authToken || !userProfile}
      onRetry={(failed) => void sendMessage(failed, { skipAddUserMessage: true })}
    />
  )

  const renderLoadingIndicator = () => (
    <div className="flex justify-start">
      <div className="py-2">
        <div className="flex gap-1">
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        {slowReply && (
          <div className="mt-1 flex items-center gap-3 text-sm text-gray-500">
            <span>{t('Your coach is still thinking…')}</span>
            <button type="button" onClick={stopWaiting} className={textButton}>
              {t('Stop waiting')}
            </button>
          </div>
        )}
      </div>
    </div>
  )

  const renderTimelineEvent = (event: TimelineEvent) => <TimelineEventView key={event.id} event={event} />

  const handleClearChatHistory = async () => {
    if (!authToken) return
    // One tap took the coach's opening message with it for good: it is written
    // once per account (ai-trainer-ops#224). Asked first, like clearing the memory.
    if (
      typeof window !== 'undefined' &&
      !window.confirm(t("Clear the whole conversation? Your coach's messages will be gone."))
    ) {
      return
    }
    const before = useAppStore.getState().chatHistory
    clearChatHistory()
    try {
      await clearChatHistoryRemote(authToken)
    } catch {
      // Cleared here only, it came back on the next load (ai-trainer-ops#272): put
      // it back now and say so, before anything that arrived meanwhile.
      setChatHistory([...before, ...useAppStore.getState().chatHistory])
      showWarning(t('The conversation could not be cleared. Try again.'))
    }
  }

  return (
    <div className={`${cardFrame} flex flex-col ${className ?? 'h-[28rem]'}`}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <span className={cardTitle}>{t('Coach Timeline')}</span>
        <div className="flex items-center gap-3">
          {coachMemory && (
            <button
              onClick={() => setShowMemory((v) => !v)}
              title={t('Coach memory')}
              aria-pressed={showMemory}
              className={showMemory ? 'text-sm font-medium text-gray-900' : textButton}
            >
              {t('Memory')}
            </button>
          )}
          {chatHistory.length > 0 && (
            <button
              onClick={() => void handleClearChatHistory()}
              title={t('Clear chat history')}
              aria-label={t('Clear chat history')}
              className={iconDeleteButton}
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Stale-refresh warning */}
      {staleRefreshWarning && (
        <p className={`mx-4 mt-2 ${note(true)}`}>
          {t("Couldn't refresh coach state — data may be slightly out of date.")}
        </p>
      )}

      {pinnedInquiry && (
        <PinnedInquiry
          inquiry={pinnedInquiry}
          onAnswer={(answer, option) => handleInquiryAnswer(pinnedInquiry, answer, option)}
          onSkip={() => handleInquirySkip(pinnedInquiry)}
        />
      )}

      {/* Input */}
      <div className="flex gap-2 border-b border-gray-100 p-3">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && enterSends()) {
              e.preventDefault()
              void sendMessage()
            }
          }}
          placeholder={t('Ask your coach...')}
          aria-label={t('Message to coach')}
          rows={1}
          className={`flex-1 resize-none ${inputClass}`}
        />
        <button
          onClick={() => void sendMessage()}
          disabled={loading || !input.trim()}
          aria-label={t('Send message')}
          className={button}
        >
          <Send size={16} />
        </button>
      </div>

      {/* Coach memory panel */}
      {showMemory && coachMemory && (
        <div className="border-b border-gray-100 px-4 py-3 text-sm text-gray-700">
          <p className="mb-1 font-medium text-gray-900">{t("Coach's notes about you")}</p>
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
